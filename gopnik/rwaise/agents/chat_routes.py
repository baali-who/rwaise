"""Flask blueprint for the in-product agent chat.

Mounts ``/rwaise/agent/api/chat`` (the JSON endpoint
``agent_chat.html`` posts to). The blueprint is registered only when
the ``BEDROCK_AGENT`` sub-flag is on (see ``plugin.py``).
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_required

from .orchestrator import run, AgentReply

log = logging.getLogger(__name__)

agent_chat_bp = Blueprint(
    "rwaise_agent_chat", __name__,
    url_prefix="/rwaise/agent",
)


_HISTORY_KEY = "rwaise_agent_history"
_TICKETS_KEY = "rwaise_agent_tickets"
_HISTORY_MAX_TURNS = 12
_TICKETS_TTL_S     = 300  # 5-minute lifetime per ticket


@agent_chat_bp.route("/api/chat", methods=["POST"])
@login_required
def chat():
    """Run one user message through the agent. Returns JSON.

    Request body:  ``{"message": "..."}``
    Response body: ``{"text": ..., "cost_usd": ..., "tools_used": [...],
                        "audit": [...], "turns": int}``
    """
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    history = session.get(_HISTORY_KEY, [])
    pending = _load_tickets()
    try:
        reply: AgentReply = run(current_user, message,
                                  history=history,
                                  pending_tickets=pending)
    except Exception as e:                                            # pragma: no cover
        log.exception("Agent run crashed: %s", e)
        return jsonify({"error": "agent error", "detail": str(e)}), 500

    # Persist a compact slice of conversation history for the next turn.
    new_history = list(history)
    new_history.append({"role": "user", "content": [{"text": message}]})
    new_history.append({"role": "assistant", "content": [{"text": reply.text}]})
    if len(new_history) > _HISTORY_MAX_TURNS:
        new_history = new_history[-_HISTORY_MAX_TURNS:]
    session[_HISTORY_KEY] = new_history
    _save_tickets(reply.pending_tickets or {})
    session.permanent = True

    return jsonify({
        "text": reply.text,
        "cost_usd": round(reply.cost_usd, 6),
        "tools_used": reply.tools_used,
        "turns": reply.turns,
        # Cap the audit returned to the browser so we don't leak full
        # tool I/O to the page; sysadmin can read the full trail in
        # /rwaise/admin/audit-trail.
        "audit": reply.audit[-8:],
    })


@agent_chat_bp.route("/api/reset", methods=["POST"])
@login_required
def reset():
    """Clear the per-user conversation history AND any pending tickets."""
    session.pop(_HISTORY_KEY, None)
    session.pop(_TICKETS_KEY, None)
    return jsonify({"ok": True})


# ─── Ticket helpers ──────────────────────────────────────────────────


def _load_tickets() -> dict:
    """Return non-expired tickets from the user's session.

    Tickets older than ``_TICKETS_TTL_S`` are dropped so a stale
    ticket can't be used a week later.
    """
    import time
    raw = session.get(_TICKETS_KEY, {}) or {}
    now = time.time()
    fresh: dict = {}
    for tid, t in raw.items():
        ts = t.get("_saved_at", 0)
        if (now - ts) <= _TICKETS_TTL_S:
            fresh[tid] = t
    return fresh


def _save_tickets(tickets: dict) -> None:
    """Persist the agent's pending tickets to the session."""
    import time
    now = time.time()
    serialised = {}
    for tid, t in (tickets or {}).items():
        # Stamp the save time so _load_tickets can expire stale entries.
        t = dict(t)
        t.setdefault("_saved_at", now)
        serialised[tid] = t
    session[_TICKETS_KEY] = serialised


@agent_chat_bp.route("/api/debug", methods=["GET"])
@login_required
def debug():
    """Health-check the agent's data sources.

    Tells you in one JSON blob:
      - Which mock flags are set (any of these → mock data)
      - Which auth modes are configured (Bedrock, CDP JWT, CDP HMAC)
      - A live spot-price call against Coinbase (XRP)
      - A live history call against Coinbase (XRP, 2 days)
      - A live authenticated call against the user's CDP keys
    Use this to diagnose "why is the agent giving me mock data?"
    """
    import os
    from . import coindesk, cdp

    def flag_state(name: str) -> dict:
        v = os.getenv(name, "").strip()
        return {"set": bool(v), "value": v if v else None,
                 "active": v.lower() in ("1", "true", "yes")}

    out: dict = {
        "mock_flags": {
            "RWAISE_COINDESK_MOCK": flag_state("RWAISE_COINDESK_MOCK"),
            "RWAISE_AGENT_MOCK":    flag_state("RWAISE_AGENT_MOCK"),
            "RWAISE_BEDROCK_MOCK":  flag_state("RWAISE_BEDROCK_MOCK"),
        },
        "auth_configured": {
            "bedrock_aws_keys": bool(os.getenv("AWS_ACCESS_KEY_ID")
                                       and os.getenv("AWS_SECRET_ACCESS_KEY")),
            "cdp_jwt":          cdp._have_cdp_jwt_key(),
            "cdp_hmac":         cdp._have_cdp_hmac_key(),
            "cdp_passphrase":   bool(os.getenv("COINBASE_API_PASSPHRASE")),
        },
        "live_tests": {},
        "verdict":    None,
    }

    # 1. Spot price for XRP — should return source="coinbase-cdp"
    try:
        sp = coindesk.spot_price("XRP")
        out["live_tests"]["xrp_spot"] = {
            "ok": True,
            "price_usd": sp.price_usd,
            "source": sp.source,
            "is_real": sp.source != "mock",
        }
    except Exception as e:
        out["live_tests"]["xrp_spot"] = {"ok": False, "error": str(e)}

    # 2. 2-day history for XRP — should be real Coinbase Exchange candles
    try:
        rows = cdp.cdp_candles("XRP", 2)
        out["live_tests"]["xrp_candles"] = {
            "ok": True,
            "count": len(rows),
            "first_close": rows[0][1] if rows else None,
        }
    except Exception as e:
        out["live_tests"]["xrp_candles"] = {"ok": False, "error": str(e)}

    # 3. Authenticated CDP call (lists 1 account)
    try:
        body = cdp.cdp_authenticated_get("/api/v3/brokerage/accounts?limit=1")
        if body is None:
            out["live_tests"]["cdp_authenticated"] = {
                "ok": False,
                "reason": "no key configured OR endpoint rejected the request",
            }
        else:
            accs = body.get("accounts") or []
            out["live_tests"]["cdp_authenticated"] = {
                "ok": True,
                "account_count": len(accs),
                "first_currency": accs[0].get("currency") if accs else None,
            }
    except Exception as e:
        out["live_tests"]["cdp_authenticated"] = {"ok": False, "error": str(e)}

    # ── Verdict ──────────────────────────────────────────────────
    active_mocks = [k for k, v in out["mock_flags"].items() if v["active"]]
    is_real      = (out["live_tests"].get("xrp_spot", {}).get("is_real") is True)
    if active_mocks:
        out["verdict"] = (
            "MOCK MODE — these env vars are forcing mock data: " +
            ", ".join(active_mocks) +
            ". Unset them in your task definition / .env, restart the app."
        )
    elif not is_real:
        out["verdict"] = (
            "LIVE MODE intended but XRP spot returned source=mock. "
            "Coinbase is unreachable from this container OR all sources "
            "failed. Check egress to api.coinbase.com:443 and "
            "api.exchange.coinbase.com:443."
        )
    else:
        out["verdict"] = (
            "LIVE — real Coinbase data flowing. "
            f"XRP spot ${out['live_tests']['xrp_spot']['price_usd']:.4f} "
            f"({out['live_tests']['xrp_spot']['source']})."
        )
    return jsonify(out)
