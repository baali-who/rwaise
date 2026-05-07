"""XLS-66-ready order book + matcher for RWAiSE listings.

Today: an application-layer order book backed by ``rwaise_orderbook_*``
tables. The matcher runs price-time priority against the open book and
settles fills via :mod:`gopnik.rwaise.services.permissioned_dex` —
which today emits a regular XRPL ``Payment`` between counterparty
wallets, gated by the on-app credential check.

When XLS-66 (Permissioned DEX) lands as a mainnet amendment, switch
the settlement seam to ``OfferCreate`` against the listing's
``permissioned_domain_id``. The :mod:`permissioned_dex` adapter
exposes a single env var seam — flip ``RWAISE_DEX_BACKEND=ledger`` and
restart.

Public API
----------
``place_order(*, listing_id, user_id, wallet, side, units, price, …)``
``cancel_order(order_id, *, user_id)``
``match(listing_id)``  — runs once per listing; idempotent.
``open_book(listing_id)``
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

log = logging.getLogger(__name__)


def place_order(*, listing_id: int, user_id: int, wallet: str, side: str,
                units: float, price: float, quote_currency: str = "USD",
                order_type: str = "limit",
                permissioned_domain_id: Optional[str] = None) -> int:
    """Create an order. Returns the new order ID.

    Caller must have already authorised the user — this function does
    NOT check credentials. The credential check belongs in routes.py
    so we can short-circuit before touching the DB.
    """
    from gopnik.rwaise.models import (
        OrderbookOrder, OrderSide, OrderStatus, OrderType,
    )
    from gopnik.models import db  # type: ignore[attr-defined]
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if units <= 0:
        raise ValueError("units must be > 0")
    if price <= 0:
        raise ValueError("price must be > 0")

    units_int = int(round(units * 100))  # rwaise_orderbook_order.units_total is BigInteger; we use 2-dp scale
    o = OrderbookOrder(
        listing_id=listing_id, user_id=user_id, wallet=wallet,
        side=OrderSide(side),
        type=OrderType(order_type),
        units_total=units_int,
        units_remaining=units_int,
        price_per_unit=price,
        quote_currency=quote_currency,
        permissioned_domain_id=permissioned_domain_id,
        status=OrderStatus.open,
    )
    db.session.add(o)
    db.session.commit()
    log.info("rwaise.orderbook order %d %s %s units=%s @ %s %s",
             o.id, side, listing_id, units, price, quote_currency)
    return o.id


def cancel_order(order_id: int, *, user_id: int) -> bool:
    from gopnik.rwaise.models import OrderbookOrder, OrderStatus
    from gopnik.models import db  # type: ignore[attr-defined]
    o = db.session.get(OrderbookOrder, order_id)
    if o is None or o.user_id != user_id:
        return False
    if o.status not in (OrderStatus.open, OrderStatus.partially_filled):
        return False
    o.status = OrderStatus.cancelled
    o.closed_at = datetime.utcnow()
    db.session.commit()
    return True


def open_book(listing_id: int) -> dict:
    """Return the current open book — used by routes + the matcher."""
    from gopnik.rwaise.models import OrderbookOrder, OrderSide, OrderStatus
    from gopnik.models import db  # type: ignore[attr-defined]
    open_states = (OrderStatus.open, OrderStatus.partially_filled)
    bids = (db.session.query(OrderbookOrder)
            .filter(OrderbookOrder.listing_id == listing_id,
                    OrderbookOrder.side == OrderSide.buy,
                    OrderbookOrder.status.in_(open_states))
            .order_by(OrderbookOrder.price_per_unit.desc(),
                      OrderbookOrder.created_at.asc())
            .all())
    asks = (db.session.query(OrderbookOrder)
            .filter(OrderbookOrder.listing_id == listing_id,
                    OrderbookOrder.side == OrderSide.sell,
                    OrderbookOrder.status.in_(open_states))
            .order_by(OrderbookOrder.price_per_unit.asc(),
                      OrderbookOrder.created_at.asc())
            .all())
    return {"bids": bids, "asks": asks}


def match(listing_id: int) -> List[int]:
    """Run price-time-priority matching against the open book.

    Returns the list of newly-created fill IDs.
    """
    from gopnik.rwaise.models import (
        OrderbookFill, OrderStatus,
    )
    from gopnik.models import db  # type: ignore[attr-defined]
    from . import permissioned_dex
    fills: List[int] = []
    book = open_book(listing_id)
    bids, asks = book["bids"], book["asks"]
    while bids and asks:
        bid, ask = bids[0], asks[0]
        if bid.price_per_unit < ask.price_per_unit:
            break
        units = min(bid.units_remaining, ask.units_remaining)
        # Price improves to the maker (ask price for an aggressive bid).
        price = ask.price_per_unit if bid.created_at > ask.created_at else bid.price_per_unit

        # Settle on-ledger via the permissioned-DEX seam.
        try:
            tx_hash = permissioned_dex.submit_offer(
                buy_order=bid, sell_order=ask, units=units, price=price,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("rwaise.orderbook settlement failed: %s", exc)
            break

        f = OrderbookFill(
            buy_order_id=bid.id, sell_order_id=ask.id,
            units=units, price_per_unit=price,
            settlement_tx_hash=tx_hash,
        )
        db.session.add(f)

        bid.units_remaining -= units
        ask.units_remaining -= units
        for o in (bid, ask):
            if o.units_remaining <= 0:
                o.status = OrderStatus.filled
                o.closed_at = datetime.utcnow()
            else:
                o.status = OrderStatus.partially_filled
        db.session.commit()
        fills.append(f.id)

        # Advance the book.
        if bid.units_remaining <= 0:
            bids.pop(0)
        if ask.units_remaining <= 0:
            asks.pop(0)
    return fills
