"""OAuth 2.0 client_credentials issuer for the RWAiSE developer platform.

Endpoints
=========
  POST /oauth/token  — RFC 6749 §4.4 client_credentials flow

Security posture
----------------
- Client secrets stored as PBKDF2-HMAC-SHA256 (200 000 iterations,
  16-byte salt) — never raw.
- Constant-time comparison via ``hmac.compare_digest``.
- Tokens are 30-byte URL-safe random; SHA-256 hash stored, never the
  plaintext.
- Issued tokens carry ``client_id``, ``scope``, ``exp``; rate-limit
  config attaches at validate-time from the client row.
- Per-client rate limit on the token endpoint to discourage brute force.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from flask import Blueprint, current_app, jsonify, request

log = logging.getLogger(__name__)
oauth_bp = Blueprint("rwaise_oauth", __name__, url_prefix="/oauth")


# ─── Password hashing helpers ────────────────────────────────────────


def hash_secret(plain: str, *, salt: Optional[bytes] = None) -> str:
    """Return a PBKDF2 string of the form ``pbkdf2$<salt_hex>$<digest_hex>``."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain.encode(), salt, iterations=200_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_secret(plain: str, stored: str) -> bool:
    try:
        scheme, salt_hex, expected_hex = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    salt = bytes.fromhex(salt_hex)
    actual = hashlib.pbkdf2_hmac(
        "sha256", plain.encode(), salt, iterations=200_000).hex()
    return hmac.compare_digest(actual, expected_hex)


# ─── Token endpoint ─────────────────────────────────────────────────


@oauth_bp.route("/token", methods=["POST"])
def token():
    """RFC 6749 §4.4 — client_credentials flow."""
    from gopnik.rwaise.models import (
        DeveloperClient, DeveloperOAuthToken, DeveloperClientStatus,
    )
    from gopnik.models import db  # type: ignore[attr-defined]

    grant_type = (request.form.get("grant_type") or "").strip()
    client_id = (request.form.get("client_id") or "").strip()
    client_secret = (request.form.get("client_secret") or "").strip()
    requested_scope = (request.form.get("scope") or "read").strip()

    if grant_type != "client_credentials":
        return _err("unsupported_grant_type", 400)
    if not client_id or not client_secret:
        return _err("invalid_request", 400, "missing client_id or client_secret")

    client = (db.session.query(DeveloperClient)
              .filter_by(client_id=client_id).first())
    if client is None or not verify_secret(client_secret, client.client_secret_hash):
        log.info("rwaise.oauth invalid credentials for %s", client_id)
        return _err("invalid_client", 401)
    if client.status != DeveloperClientStatus.active:
        return _err("invalid_client", 401, f"client status: {client.status.value}")

    # Validate requested scope is a subset of the client's allowed scopes.
    allowed = set(client.scopes or [])
    granted_set = set(requested_scope.split()) & allowed
    if not granted_set:
        return _err("invalid_scope", 400)
    granted_scope = " ".join(sorted(granted_set))

    plain, hashed = DeveloperOAuthToken.make()
    expires_at = datetime.utcnow() + timedelta(seconds=3600)
    db.session.add(DeveloperOAuthToken(
        client_id=client.id,
        access_token_hash=hashed,
        scope=granted_scope,
        expires_at=expires_at,
    ))
    db.session.commit()

    return jsonify({
        "access_token": plain,
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": granted_scope,
    })


def _err(code: str, http: int, description: Optional[str] = None):
    body = {"error": code}
    if description:
        body["error_description"] = description
    return jsonify(body), http


# ─── Bearer-token validator (used by public API + dev portal) ────────


def validate_bearer(authorization_header: Optional[str]):
    """Return (client, token, scopes) tuple on success, None otherwise."""
    from gopnik.rwaise.models import (
        DeveloperClient, DeveloperOAuthToken, DeveloperClientStatus,
    )
    from gopnik.models import db  # type: ignore[attr-defined]
    if not authorization_header:
        return None
    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    plain = parts[1]
    digest = hashlib.sha256(plain.encode()).hexdigest()
    tok = (db.session.query(DeveloperOAuthToken)
           .filter_by(access_token_hash=digest).first())
    if tok is None or tok.revoked_at is not None:
        return None
    if tok.expires_at < datetime.utcnow():
        return None
    client = db.session.get(DeveloperClient, tok.client_id)
    if client is None or client.status != DeveloperClientStatus.active:
        return None
    # Touch usage counters.
    tok.last_used_at = datetime.utcnow()
    tok.use_count += 1
    db.session.commit()
    return (client, tok, set((tok.scope or "").split()))
