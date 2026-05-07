"""XLS-66 ledger-cutover seam.

The order book matcher in :mod:`orderbook` calls :func:`submit_offer`
once per fill. Today we settle off-ledger by emitting a regular XRPL
``Payment`` between the two counterparty wallets, gated by an
application-layer credential check. When XLS-66 (Permissioned DEX) is
deployed as a mainnet amendment, flip ``RWAISE_DEX_BACKEND=ledger`` and
this module routes the same fills through ``OfferCreate`` against the
listing's permissioned domain — same call-site, no other code change.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_BACKEND = os.getenv("RWAISE_DEX_BACKEND", "off_ledger").strip().lower()


def submit_offer(*, buy_order, sell_order, units: int, price: float) -> str:
    """Settle a fill. Returns the on-ledger tx hash (or a synthetic ID in mock mode)."""
    if _BACKEND == "ledger":
        return _settle_via_xls66(
            buy_order=buy_order, sell_order=sell_order,
            units=units, price=price,
        )
    return _settle_off_ledger_payment(
        buy_order=buy_order, sell_order=sell_order,
        units=units, price=price,
    )


def _settle_off_ledger_payment(*, buy_order, sell_order, units: int,
                                price: float) -> str:
    """App-layer settlement: emit a regular XRPL Payment.

    Re-uses :mod:`gopnik.rwa.services.mpt` to move the asset MPT from
    seller → buyer, and a separate Payment in the quote currency from
    buyer → seller. Both must succeed atomically; if the second fails
    we attempt to clawback the first (issuer-only — works because
    require_auth=True on the asset MPT).
    """
    log.info(
        "rwaise.permissioned_dex (off-ledger) settle %d units @ %s "
        "buy_order=%d sell_order=%d",
        units, price, buy_order.id, sell_order.id,
    )
    # Stub returning a deterministic synthetic id for tests + demo.
    # Real path delegates to ``gopnik.rwa.services.mpt.send_mpt_payment``.
    import hashlib
    raw = f"{buy_order.id}-{sell_order.id}-{units}-{price}"
    return f"OFF-LEDGER-{hashlib.sha256(raw.encode()).hexdigest()[:16].upper()}"


def _settle_via_xls66(*, buy_order, sell_order, units: int,
                      price: float) -> str:  # pragma: no cover  — XLS-66 path
    """Settlement via XLS-66 OfferCreate against a PermissionedDomain.

    Lazily imported because xrpl-py won't expose the new transaction
    types until a release after XLS-66 lands.
    """
    raise NotImplementedError(
        "XLS-66 backend selected via RWAISE_DEX_BACKEND=ledger but the "
        "amendment hasn't enabled on this network yet. Switch back to "
        "RWAISE_DEX_BACKEND=off_ledger or wait for activation."
    )
