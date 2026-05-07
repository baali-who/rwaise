"""Feature-flag helpers for the RWAiSE plugin.

Iter-15 (production hardening)
==============================
Original scaffold was env-var-only. Production deployments need the
admin to toggle features without redeploying the Fargate task. We now
support a three-layer cascade:

  1. DB row in ``rwaise_feature_flag`` (cluster-wide truth)
  2. Redis cache (cluster-shared, ~1 ms)
  3. Env var fallback (cold-start / DB outage / tests)

Env var ``RWAISE_ENABLED`` is the **floor**: when it is off, the master
switch returns False regardless of DB state. This keeps the operator's
big red kill button accessible by SSH + task-definition redeploy.

Sub-flags::

  RWAISE_ENABLED                  master switch (env-var floor)
  RWAISE_FEATURE_WIZARD           the 9-step tokenisation wizard
  RWAISE_FEATURE_AI_COPILOT       Bedrock-Claude wizard co-pilot
  RWAISE_FEATURE_CREDENTIALS      layered XLS-65 credentials
  RWAISE_FEATURE_ORDERBOOK        XLS-66-ready order book
  RWAISE_FEATURE_ESCROW           MPT-aware escrow
  RWAISE_FEATURE_REDEMPTION       4-eyes redemption pipeline
  RWAISE_FEATURE_X402             HTTP 402 payment middleware
  RWAISE_FEATURE_BEDROCK_AGENT    in-product Bedrock agent
  RWAISE_FEATURE_DEVELOPER_API    OAuth + paid public API + dev portal
  RWAISE_FEATURE_ODL              ODL routing pilot
  RWAISE_FEATURE_HSM_SIGNER       CloudHSM signer for issuances
"""
from __future__ import annotations

import logging
import os
import time
from typing import Final, Optional

log = logging.getLogger(__name__)

_TRUTHY: Final = frozenset({"1", "true", "yes", "on", "y", "t"})
_LOCAL_TTL_S: Final = 5.0  # In-process cache; trades freshness for template render speed
_LOCAL_CACHE: dict[str, tuple[float, bool]] = {}

SUB_FLAGS: Final = (
    "WIZARD", "AI_COPILOT", "CREDENTIALS", "ORDERBOOK", "ESCROW",
    "REDEMPTION", "X402", "BEDROCK_AGENT", "DEVELOPER_API", "ODL",
    "HSM_SIGNER",
)


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _env_floor() -> bool:
    """Operator's hard floor — DB cannot override an env-var-disabled deploy."""
    return _env_truthy("RWAISE_ENABLED")


# ─── Layer 2: Redis (cluster-shared) ─────────────────────────────────


def _redis_lookup(key: str) -> Optional[bool]:
    try:
        from flask import current_app
        client = current_app.extensions.get("redis") if current_app else None
        if client is None:
            return None
        raw = client.get(f"rwaise:flag:{key}")
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return raw == "1"
    except Exception:
        return None


def _redis_set(key: str, value: bool, ttl_s: int = 300) -> None:
    try:
        from flask import current_app
        client = current_app.extensions.get("redis") if current_app else None
        if client is None:
            return
        client.setex(f"rwaise:flag:{key}", ttl_s, "1" if value else "0")
    except Exception:
        pass


# ─── Layer 1: DB (authoritative) ─────────────────────────────────────


def _db_lookup(key: str) -> Optional[bool]:
    """Read from rwaise_feature_flag. None on miss / DB down / no app context."""
    try:
        from flask import has_app_context
        if not has_app_context():
            return None
        from .models import FeatureFlag
        from gopnik.models import db  # type: ignore[attr-defined]
        row = db.session.query(FeatureFlag).filter_by(key=key).first()
        return bool(row.enabled) if row is not None else None
    except Exception as exc:  # noqa: BLE001
        log.debug("feature_flag DB lookup for %s failed: %s", key, exc)
        return None


# ─── Cascade resolver ────────────────────────────────────────────────


def _resolve(key: str, default: bool) -> bool:
    now = time.monotonic()
    cached = _LOCAL_CACHE.get(key)
    if cached and (now - cached[0]) < _LOCAL_TTL_S:
        return cached[1]

    val = _redis_lookup(key)
    if val is None:
        val = _db_lookup(key)
        if val is not None:
            _redis_set(key, val)
    if val is None:
        val = _env_truthy(key, default=default)

    _LOCAL_CACHE[key] = (now, val)
    return val


# ─── Public API ──────────────────────────────────────────────────────


def is_enabled() -> bool:
    """Master switch — env var floor + DB override."""
    if not _env_floor():
        return False
    return _resolve("RWAISE_ENABLED", default=True)


def feature_on(name: str, *, default: bool = True) -> bool:
    if not is_enabled():
        return False
    return _resolve(f"RWAISE_FEATURE_{name.upper()}", default=default)


def set_feature(name: str, value: bool, *,
                actor_user_id: Optional[int] = None,
                reason: str = "") -> bool:
    """Admin toggle — DB write + Redis broadcast + local invalidation."""
    from .models import FeatureFlag, FeatureFlagAudit
    from gopnik.models import db  # type: ignore[attr-defined]
    key = name if name.startswith("RWAISE_") else f"RWAISE_FEATURE_{name.upper()}"
    row = db.session.query(FeatureFlag).filter_by(key=key).first()
    if row is None:
        row = FeatureFlag(key=key, enabled=value)
        db.session.add(row)
    else:
        row.enabled = value
    db.session.add(FeatureFlagAudit(
        key=key, value=value,
        actor_user_id=actor_user_id, reason=reason,
    ))
    db.session.commit()
    _redis_set(key, value)
    _LOCAL_CACHE.pop(key, None)
    log.info("rwaise.feature_flag set %s=%s actor=%s reason=%s",
             key, value, actor_user_id, reason)
    return value


def all_flags() -> dict[str, bool]:
    if not is_enabled():
        return {"RWAISE_ENABLED": False}
    out: dict[str, bool] = {"RWAISE_ENABLED": True}
    for n in SUB_FLAGS:
        out[f"RWAISE_FEATURE_{n}"] = feature_on(n)
    return out


def invalidate_local_cache() -> None:
    """Test hook."""
    _LOCAL_CACHE.clear()
