"""x402 Payer that signs with a Gopnik ``WalletUser`` row.

Existing :class:`gopnik.rwaise.x402.client.XrplPayer` takes a raw seed
in ctor — fine for SDK clients, dangerous for the in-product agent
because we'd have to store the seed somewhere callable. This Payer
fetches the seed from the user's wallet table on each call, sealing
the seed inside the request scope.

Safety
------
* **Per-call USD cap** — refuses to broadcast if the calculated USD
  cost exceeds ``max_pay_usd`` (default $0.10).
* **Per-session USD cap** — running counter on the agent session;
  cumulative spend across calls can't exceed
  ``RWAISE_AGENT_SESSION_CAP_USD`` (default $1.00).
* **Mock mode** — when no seed is decryptable (e.g. the user imported
  a read-only wallet) or env says so, returns a deterministic mock
  tx hash so the demo never fails.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class SpendingCap:
    """Per-session running total of x402 dollars spent.

    Lives on the :class:`PaymentSession` and is consulted by every
    Payer-backed tool call. Reset by the orchestrator when the user
    starts a new chat session.
    """
    max_session_usd: float = 1.00
    spent_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self, amount_usd: float) -> None:
        with self._lock:
            if self.spent_usd + amount_usd > self.max_session_usd:
                raise PermissionError(
                    f"per-session spending cap exceeded: "
                    f"${self.spent_usd + amount_usd:.4f} > ${self.max_session_usd:.2f}")
            self.spent_usd += amount_usd

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self.max_session_usd - self.spent_usd)


class WalletUserPayer:
    """x402 Payer backed by a Gopnik :class:`WalletUser` row.

    Conforms to the ``Payer`` Protocol from
    :mod:`gopnik.rwaise.x402.client`.
    """

    def __init__(self, *, wallet_user, cap: SpendingCap,
                 rpc_url: Optional[str] = None,
                 max_pay_usd_per_call: float = 0.10):
        self._wallet = wallet_user
        self._cap = cap
        self._rpc = rpc_url or os.getenv("XRPL_RPC_URL", "https://xrplcluster.com")
        self._max_per_call = max_pay_usd_per_call

    def pay(self, *, destination: str, amount: str, currency: str,
            issuer: Optional[str], memo: str) -> str:
        """Sign + broadcast an XRPL Payment as the wallet's owner.

        Returns the tx hash. Raises ``PermissionError`` if the call
        would exceed the per-call or per-session caps.
        """
        cost = self._estimate_usd(amount, currency)
        if cost > self._max_per_call:
            raise PermissionError(
                f"per-call cap ${self._max_per_call:.4f} would be exceeded by "
                f"${cost:.4f}")
        # Reserve from the session budget *before* broadcasting so a
        # crash mid-flight doesn't leak budget.
        self._cap.reserve(cost)

        if _force_mock() or not _has_seed(self._wallet):
            log.info("WalletUserPayer: mock pay → %s %s to %s",
                     amount, currency, destination)
            return _mock_tx_hash(self._wallet, destination, amount, memo)

        return self._real_pay(destination=destination, amount=amount,
                              currency=currency, issuer=issuer, memo=memo)

    # ── helpers ──

    def _estimate_usd(self, amount: str, currency: str) -> float:
        if currency.upper() == "XRP":
            try:
                drops = int(amount)
            except (TypeError, ValueError):
                drops = 0
            return drops / 1_000_000 * 0.50  # $0.50/XRP demo conversion
        try:
            return float(amount)
        except (TypeError, ValueError):
            return 0.0

    def _real_pay(self, *, destination: str, amount: str, currency: str,
                  issuer: Optional[str], memo: str) -> str:        # pragma: no cover
        from xrpl.clients import JsonRpcClient
        from xrpl.models.transactions import Memo, Payment
        from xrpl.transaction import autofill_and_sign, submit_and_wait
        from xrpl.wallet import Wallet

        seed = _decrypt_seed(self._wallet)
        if not seed:
            raise PermissionError("Wallet seed unavailable for signing")

        client = JsonRpcClient(self._rpc)
        wallet = Wallet.from_seed(seed)
        if currency.upper() == "XRP":
            amt = amount
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
        return resp.result.get("hash") or resp.result.get("tx_json", {}).get("hash", "")


# ─── Module-level helpers (also exported for unit tests) ─────────────


def _force_mock() -> bool:
    return os.getenv("RWAISE_AGENT_MOCK", "").strip().lower() in ("1", "true", "yes") \
        or os.getenv("RWAISE_BEDROCK_MOCK", "").strip().lower() in ("1", "true", "yes")


def _has_seed(w) -> bool:
    return bool(getattr(w, "seed_encrypted", None) or getattr(w, "encrypted_seed", None))


def _decrypt_seed(w) -> Optional[str]:
    enc = getattr(w, "seed_encrypted", None) or getattr(w, "encrypted_seed", None)
    if not enc:
        return None
    # Gopnik's wallet table stores either base64-encoded seeds or
    # AES-GCM ciphertext blobs (see SecretManager). For the agent we
    # only want the simple base64 path; anything else falls through to
    # the mock signer rather than risking a half-decrypted seed in
    # logs.
    try:
        decoded = base64.b64decode(enc).decode()
        if decoded.startswith("s") and len(decoded) >= 25:
            return decoded
    except Exception:                                                 # pragma: no cover
        pass
    return None


def _mock_tx_hash(wallet, destination: str, amount: str, memo: str) -> str:
    import hashlib
    blob = f"{getattr(wallet, 'address', '?')}|{destination}|{amount}|{memo}".encode()
    return hashlib.sha256(blob).hexdigest().upper()
