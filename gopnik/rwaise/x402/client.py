"""x402 client helpers — call a 402-protected API and pay automatically.

Used by the public-API SDK examples and by the developer portal's
"Try it" button. The default :class:`XrplPayer` signs with a local
seed; production clients pass their own ``Payer`` (e.g. a KMS-backed
or Xaman-prompted variant).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol

import requests

log = logging.getLogger(__name__)


class Payer(Protocol):  # pragma: no cover  — interface
    def pay(self, *, destination: str, amount: str, currency: str,
            issuer: Optional[str], memo: str) -> str: ...


@dataclass
class XrplPayer:
    """Local-seed payer. Convenient for SDKs / tests; not for prod wallets."""
    seed: str
    rpc_url: str = ""

    def pay(self, *, destination: str, amount: str, currency: str,
            issuer: Optional[str], memo: str) -> str:
        from xrpl.clients import JsonRpcClient
        from xrpl.models.transactions import Memo, Payment
        from xrpl.transaction import autofill_and_sign, submit_and_wait
        from xrpl.wallet import Wallet
        rpc = self.rpc_url or os.getenv("XRPL_RPC_URL", "https://xrplcluster.com")
        client = JsonRpcClient(rpc)
        wallet = Wallet.from_seed(self.seed)
        if currency.upper() == "XRP":
            amt = amount  # drops
        else:
            amt = {"currency": currency, "issuer": issuer, "value": amount}
        tx = Payment(
            account=wallet.classic_address,
            destination=destination,
            amount=amt,
            memos=[Memo(memo_data=memo.encode().hex().upper())],
        )
        signed = autofill_and_sign(tx, client, wallet)
        resp = submit_and_wait(signed, client, wallet)
        return resp.result["hash"]


def pay_and_retry(url: str, *, payer: Payer,
                  method: str = "GET",
                  json_body: Optional[dict] = None,
                  timeout: float = 30.0) -> requests.Response:
    """Call ``url``, pay automatically on 402, retry once."""
    sess = requests.Session()
    request_id = str(uuid.uuid4())

    # 1. First call — discover the price.
    resp = sess.request(method, url, json=json_body,
                        headers={"X-Payment-Request-Id": request_id},
                        timeout=timeout)
    if resp.status_code != 402:
        return resp

    body = resp.json()
    req = body.get("payment_requirements", {})
    log.info("x402 quoted %s %s for %s", req.get("amount"),
             req.get("currency"), url)

    # 2. Pay on-ledger.
    tx_hash = payer.pay(
        destination=req["destination"],
        amount=req["amount"],
        currency=req.get("currency", "XRP"),
        issuer=req.get("issuer"),
        memo=req.get("memo", "rwaise.api"),
    )

    # 3. Retry with X-Payment header.
    payload = base64.b64encode(json.dumps({
        "scheme": "xrpl-payment", "tx_hash": tx_hash,
    }).encode()).decode()
    headers = {
        "X-Payment": f"xrpl-payment:{payload}",
        "X-Payment-Request-Id": request_id,
    }
    return sess.request(method, url, json=json_body,
                        headers=headers, timeout=timeout)
