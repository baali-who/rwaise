"""x402 facilitator — verifies on-ledger payment proofs.

The protocol's ``X-Payment`` header carries a base64-encoded payload
shaped like::

    {"scheme": "xrpl-payment", "tx_hash": "<64-hex>"}

The facilitator looks up the tx on the XRP Ledger, validates that:

  * the tx is validated (final),
  * the destination matches the expected fee receiver,
  * the delivered amount matches the expected price (XRP drops or token amount),
  * the Memo carries the expected prefix (associates payment with this request).

Returns a ``receipt`` dict on success; raises :class:`X402Error` otherwise.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional

from .middleware import X402Error

log = logging.getLogger(__name__)


def _xrpl_rpc_url() -> str:
    return (os.getenv("XRPL_RPC_URL")
            or os.getenv("MPT_RPC_URL")
            or os.getenv("XRPL_ENDPOINT_MAINNET")
            or "https://xrplcluster.com")


def _decode_payment_header(payment_header: str) -> dict:
    if ":" not in payment_header:
        raise X402Error("malformed X-Payment header (expected scheme:payload)")
    scheme, payload = payment_header.split(":", 1)
    if scheme != "xrpl-payment":
        raise X402Error(f"unsupported payment scheme {scheme!r}")
    try:
        decoded = base64.b64decode(payload).decode()
        body = json.loads(decoded)
    except Exception as exc:  # noqa: BLE001
        raise X402Error(f"failed to decode X-Payment payload: {exc}") from exc
    tx_hash = (body or {}).get("tx_hash") or ""
    if not (isinstance(tx_hash, str) and len(tx_hash) == 64):
        raise X402Error("missing or invalid tx_hash in X-Payment payload")
    return body


def _fetch_tx(tx_hash: str) -> dict:
    """Query XRPL ``tx`` RPC. Raises X402Error on RPC / network failure."""
    try:
        from xrpl.clients import JsonRpcClient
        from xrpl.models.requests import Tx
        client = JsonRpcClient(_xrpl_rpc_url())
        resp = client.request(Tx(transaction=tx_hash, binary=False))
        return resp.result or {}
    except Exception as exc:  # noqa: BLE001
        raise X402Error(f"XRPL tx lookup failed: {exc}") from exc


def _decode_memo_data(memos: list) -> str:
    """Read the first Memo's MemoData and hex-decode it. '' if missing."""
    if not memos:
        return ""
    try:
        memo_obj = memos[0].get("Memo", {})
        data_hex = memo_obj.get("MemoData", "")
        if data_hex:
            return bytes.fromhex(data_hex).decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass
    return ""


def verify_payment_proof(*, payment_header: str,
                         expected_amount: str,
                         expected_currency: str,
                         expected_destination: str,
                         expected_memo_prefix: str) -> dict:
    """Validate the supplied X-Payment header. Returns a receipt dict.

    Raises :class:`X402Error` for any failure mode.
    """
    body = _decode_payment_header(payment_header)
    tx_hash: str = body["tx_hash"]
    tx = _fetch_tx(tx_hash)

    if not tx.get("validated"):
        raise X402Error("tx is not yet validated on the ledger")

    # Destination check.
    dest = tx.get("Destination")
    if dest != expected_destination:
        raise X402Error(
            f"tx destination {dest!r} ≠ expected {expected_destination!r}")

    # Memo prefix check.
    memo_text = _decode_memo_data(tx.get("Memos", []))
    if expected_memo_prefix and not memo_text.startswith(expected_memo_prefix):
        raise X402Error(
            f"tx memo {memo_text!r} does not start with {expected_memo_prefix!r}")

    # Amount check.
    delivered = tx.get("delivered_amount") or tx.get("Amount")
    if expected_currency.upper() == "XRP":
        # delivered for XRP is a drops string.
        try:
            paid_drops = int(delivered)  # XRP drops
        except (TypeError, ValueError) as exc:
            raise X402Error("malformed XRP amount in tx") from exc
        if paid_drops < int(expected_amount):
            raise X402Error(
                f"underpaid: {paid_drops} drops < expected {expected_amount}")
    else:
        # Issued currency: delivered is a dict {currency, issuer, value}.
        if not isinstance(delivered, dict):
            raise X402Error("expected an issued-currency Amount object")
        if delivered.get("currency", "").upper() != expected_currency.upper():
            raise X402Error("currency mismatch")
        if float(delivered.get("value", "0")) < float(expected_amount):
            raise X402Error("underpaid (token amount)")

    return {
        "tx_hash": tx_hash,
        "destination": dest,
        "amount": str(delivered),
        "currency": expected_currency,
        "memo": memo_text,
    }
