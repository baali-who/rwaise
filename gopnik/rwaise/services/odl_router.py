"""ODL (On-Demand Liquidity) routing pilot.

Cross-currency value transfer using a tokenised RWA as the bridge.
This is a research module — it's NOT meant to compete with the
production ODL stack, but to demonstrate that a tokenised RWA's
liquidity can be tapped as ephemeral routing capacity.

A route looks like::

    USD --(buy)--> RWAISE_TBILL --(sell)--> EUR

The route's ``rwaise_odl_route`` row carries the bridge MPT issuance,
typical spread, and acceptable notional bounds. :func:`quote` returns
the best available route for a (source, dest, notional) tuple.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def quote(*, source: str, dest: str, notional_usd: float) -> Optional[dict]:
    """Find the best route. Returns a dict or None when no route fits."""
    from gopnik.rwaise.models import ODLRoute
    from gopnik.models import db  # type: ignore[attr-defined]
    candidates = (db.session.query(ODLRoute)
                  .filter(ODLRoute.source_currency == source.upper(),
                          ODLRoute.destination_currency == dest.upper(),
                          ODLRoute.enabled.is_(True),
                          ODLRoute.notional_usd_min <= notional_usd,
                          ODLRoute.notional_usd_max >= notional_usd)
                  .order_by(ODLRoute.typical_spread_bps.asc())
                  .all())
    if not candidates:
        return None
    best = candidates[0]
    return {
        "source": source.upper(),
        "destination": dest.upper(),
        "bridge_symbol": best.bridge_symbol,
        "bridge_mpt_issuance_id": best.bridge_mpt_issuance_id,
        "typical_spread_bps": best.typical_spread_bps,
        "estimated_total_cost_bps": best.typical_spread_bps + 5,  # +5 for execution slippage
    }


def execute(*, route_id: int, sender: str, recipient: str,
            notional_usd: float) -> dict:  # pragma: no cover — live ledger
    """Execute the route. Out of scope for the hackathon demo; emits a stub."""
    raise NotImplementedError(
        "ODL execution requires live source/dest liquidity venues. "
        "Use the quote endpoint for the demo; flip RWAISE_FEATURE_ODL=on in iter-16."
    )
