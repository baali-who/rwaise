"""HTTP 402 Payment Required middleware for the XRP Ledger.

Implements the Coinbase / x402 working-group convention: an API
endpoint that returns 402 with a JSON ``payment_requirements`` block;
the client pays on-ledger and retries with an ``X-Payment``
header; the facilitator verifies the proof and the request proceeds.

Spec reference
==============
- Header (request):  ``X-Payment: <scheme>:<base64-payload>``
- Header (response): ``X-Payment-Receipt: <scheme>:<tx_hash>``
- 402 body::

    {
      "version": "0.4",
      "scheme": "xrpl-payment",
      "payment_requirements": {
        "amount":   "5000",            // drops, or "0.5" for token amount
        "currency": "XRP",             // or "RLUSD" / MPT issuance ID
        "issuer":   null,              // for currency=="XRP"
        "destination": "r…platform-fee-receiver…",
        "memo": "rwaise.api:<request_id>"
      },
      "request_id": "<uuid>"
    }

Usage
=====
::

    from gopnik.rwaise.x402.middleware import x402_protected, PaymentRequirement

    @app.route("/v1/expensive")
    @x402_protected(PaymentRequirement(amount="5000", currency="XRP",
                                        memo="rwaise.api"))
    def expensive():
        return jsonify({"ok": True})
"""
from __future__ import annotations

import functools
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from flask import current_app, g, jsonify, make_response, request

log = logging.getLogger(__name__)


class X402Error(Exception):
    """Base class for x402 protocol errors."""


@dataclass
class PaymentRequirement:
    """The contract the server publishes on a 402."""
    amount: str          # drops for XRP, decimal-as-string for tokens
    currency: str = "XRP"
    issuer: Optional[str] = None
    destination: Optional[str] = None       # falls back to RWAISE_FEE_RECEIVER
    memo: str = "rwaise.api"
    scheme: str = "xrpl-payment"
    version: str = "0.4"


def _resolve_destination(req: PaymentRequirement) -> str:
    if req.destination:
        return req.destination
    dest = (current_app.config.get("RWAISE_FEE_RECEIVER")
            or os.getenv("RWAISE_FEE_RECEIVER"))
    if not dest:
        raise X402Error(
            "x402 server has no payment destination configured. Set "
            "RWAISE_FEE_RECEIVER in env or pass destination=...")
    return dest


def x402_protected(requirement: PaymentRequirement) -> Callable:
    """Decorator: gate a Flask view behind an HTTP 402 payment.

    Verifies the ``X-Payment`` header on each request via
    :mod:`facilitator.verify_payment_proof`. On success, runs the view
    and stamps an ``X-Payment-Receipt`` header on the response.
    """
    def _wrap(view: Callable) -> Callable:
        @functools.wraps(view)
        def _inner(*args, **kwargs):
            from .facilitator import verify_payment_proof

            payment = request.headers.get("X-Payment")
            request_id = request.headers.get("X-Payment-Request-Id") \
                          or str(uuid.uuid4())

            if not payment:
                # Quote a price.
                req = PaymentRequirement(
                    amount=requirement.amount,
                    currency=requirement.currency,
                    issuer=requirement.issuer,
                    destination=_resolve_destination(requirement),
                    memo=f"{requirement.memo}:{request_id}",
                    scheme=requirement.scheme,
                    version=requirement.version,
                )
                body = {
                    "version": req.version,
                    "scheme": req.scheme,
                    "payment_requirements": {
                        k: v for k, v in asdict(req).items()
                        if k not in ("scheme", "version")
                    },
                    "request_id": request_id,
                }
                resp = jsonify(body)
                resp.status_code = 402
                resp.headers["X-Payment-Request-Id"] = request_id
                return resp

            # Verify the proof.
            try:
                receipt = verify_payment_proof(
                    payment_header=payment,
                    expected_amount=requirement.amount,
                    expected_currency=requirement.currency,
                    expected_destination=_resolve_destination(requirement),
                    expected_memo_prefix=requirement.memo,
                )
            except X402Error as exc:
                log.info("x402 verify failed: %s", exc)
                resp = jsonify({"error": "invalid_payment", "detail": str(exc)})
                resp.status_code = 402
                return resp

            # Run the view; stamp the receipt.
            g.x402_payment = receipt
            response = make_response(view(*args, **kwargs))
            response.headers["X-Payment-Receipt"] = (
                f"{requirement.scheme}:{receipt['tx_hash']}"
            )
            return response
        return _inner
    return _wrap
