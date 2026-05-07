"""Public API for the RWAiSE developer platform.

Two auth modes per endpoint:

  • OAuth Bearer (RFC 6749 §4.4) — the client identified itself with a
    registered ``client_id`` / ``client_secret`` and got back a token.
    Calls are metered against the client's daily spend cap.

  • x402 ad-hoc payment — anonymous callers pay per-request on the
    ledger. Useful for one-off integrations without a registered app.

Endpoints
=========
  GET  /v1/issuances              list public issuance metadata
  GET  /v1/issuances/<id>         issuance detail
  GET  /v1/marketplace            cap-table-aware listings
  GET  /v1/orderbook/<listing_id> open book snapshot
  POST /v1/odl/quote              cross-currency route quote
  GET  /v1/credentials/types      credential catalogue
"""
from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Optional

from flask import Blueprint, current_app, g, jsonify, request

from gopnik.rwaise.x402 import x402_protected, PaymentRequirement
from ..oauth import validate_bearer

log = logging.getLogger(__name__)
public_api_bp = Blueprint(
    "rwaise_public_api", __name__, url_prefix="/v1")


# ─── Auth decorator (OAuth OR x402) ──────────────────────────────────


def _resolve_caller():
    """Returns one of:
       - ('oauth', client, token, scopes)
       - ('x402', None, None, None)   set after middleware completes
       - (None, None, None, None)     unauthenticated
    """
    bearer = validate_bearer(request.headers.get("Authorization"))
    if bearer is not None:
        client, token, scopes = bearer
        return ("oauth", client, token, scopes)
    if request.headers.get("X-Payment"):
        return ("x402", None, None, None)
    return (None, None, None, None)


def authenticated(scope_required: str = "read"):
    """Decorator: require either OAuth bearer with the given scope OR x402."""
    def _wrap(fn):
        @wraps(fn)
        def _inner(*args, **kwargs):
            mode, client, token, scopes = _resolve_caller()
            if mode == "oauth":
                if scope_required and scope_required not in (scopes or set()):
                    return jsonify({"error": "insufficient_scope",
                                    "scope_required": scope_required}), 403
                g.rwaise_caller = {"mode": "oauth",
                                   "client_id": client.client_id,
                                   "scopes": list(scopes or [])}
                _log_call(client_id=client.id, paid=False)
                return fn(*args, **kwargs)
            if mode == "x402":
                # The x402_protected wrapper has verified the proof.
                g.rwaise_caller = {"mode": "x402"}
                receipt = getattr(g, "x402_payment", {})
                _log_call(client_id=None, paid=True,
                          payment_tx=receipt.get("tx_hash"))
                return fn(*args, **kwargs)
            # Neither mode → return 401 (with WWW-Authenticate hint).
            resp = jsonify({"error": "unauthorized",
                            "hint": "use Bearer token (OAuth) or X-Payment (x402)"})
            resp.status_code = 401
            resp.headers["WWW-Authenticate"] = (
                'Bearer realm="rwaise", error="invalid_request"')
            return resp
        return _inner
    return _wrap


def _log_call(*, client_id: Optional[int], paid: bool,
              payment_tx: Optional[str] = None) -> None:
    from gopnik.rwaise.models import DeveloperAPICallLog
    from gopnik.models import db  # type: ignore[attr-defined]
    try:
        db.session.add(DeveloperAPICallLog(
            client_id=client_id or 0,
            method=request.method,
            path=request.path,
            status_code=200,  # set after the view runs in iter-16
            latency_ms=0,
            paid=paid,
            payment_scheme="xrpl-payment" if paid else None,
            payment_tx_hash=payment_tx,
            request_id=request.headers.get("X-Payment-Request-Id"),
        ))
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("rwaise.public_api log_call failed: %s", exc)


# ─── Endpoints ──────────────────────────────────────────────────────


# Pricing — drops per call. Centralised so we can tune in one place.
PRICE_LIST_DROPS = 5_000          # 0.005 XRP per list call
PRICE_DETAIL_DROPS = 10_000       # 0.01 XRP per detail call


@public_api_bp.route("/issuances", methods=["GET"])
@x402_protected(PaymentRequirement(amount=str(PRICE_LIST_DROPS), currency="XRP",
                                    memo="rwaise.api:issuances"))
@authenticated("read")
def list_issuances():
    from gopnik.rwaise.models import MPTIssuance
    from gopnik.models import db  # type: ignore[attr-defined]
    rows = (db.session.query(MPTIssuance)
            .order_by(MPTIssuance.created_at.desc()).limit(50).all())
    return jsonify({"issuances": [
        {"issuance_id": r.issuance_id, "issuer": r.issuer_account,
         "status": r.status.value if r.status else None,
         "metadata": r.metadata_json}
        for r in rows
    ]})


@public_api_bp.route("/issuances/<int:issuance_id>", methods=["GET"])
@x402_protected(PaymentRequirement(amount=str(PRICE_DETAIL_DROPS), currency="XRP",
                                    memo="rwaise.api:issuance"))
@authenticated("read")
def get_issuance(issuance_id: int):
    from gopnik.rwaise.models import MPTIssuance
    from gopnik.models import db  # type: ignore[attr-defined]
    row = db.session.get(MPTIssuance, issuance_id)
    if row is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({
        "issuance_id": row.issuance_id, "issuer": row.issuer_account,
        "status": row.status.value if row.status else None,
        "asset_scale": row.asset_scale,
        "transfer_fee_bps": row.transfer_fee,
        "maximum_amount": row.maximum_amount,
        "metadata": row.metadata_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })


@public_api_bp.route("/credentials/types", methods=["GET"])
@authenticated("read")
def credential_catalogue():
    """Free endpoint — credentials catalogue is reference data."""
    from gopnik.rwaise.services.credential_issuer import CATALOGUE
    return jsonify({"catalogue": list(CATALOGUE)})


@public_api_bp.route("/odl/quote", methods=["POST"])
@x402_protected(PaymentRequirement(amount=str(PRICE_DETAIL_DROPS), currency="XRP",
                                    memo="rwaise.api:odl_quote"))
@authenticated("read")
def odl_quote():
    from gopnik.rwaise.services import odl_router
    body = request.get_json(silent=True) or {}
    quote = odl_router.quote(
        source=body.get("source", ""),
        dest=body.get("destination", ""),
        notional_usd=float(body.get("notional_usd", 0)),
    )
    if quote is None:
        return jsonify({"error": "no_route"}), 404
    return jsonify(quote)
