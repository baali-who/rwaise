"""Seed 8 realistic-by-2030 RWA issuances for the hackathon demo.

Not run automatically — invoked by ``flask rwaise seed-demo``.
"""
from __future__ import annotations

import logging
import secrets

log = logging.getLogger(__name__)

DEMO_ASSETS: tuple[dict, ...] = (
    {"symbol": "RWAISE-USTBL", "name": "US T-Bill Strip 2030",
     "asset_class": "debt",
     "spv_name": "RWAISE TBill Strip 2030 LLC",
     "spv_jurisdiction": "US-DE",
     "valuation_usd": 12_500_000,
     "transfer_fee_bps": 25,
     "metadata": {"maturity": "2030-09-15", "coupon_bps": 425}},
    {"symbol": "RWAISE-EUBND", "name": "EU Sovereign Green Bond 2032",
     "asset_class": "debt",
     "spv_name": "RWAISE EU Green Bond 2032 SARL",
     "spv_jurisdiction": "LU",
     "valuation_usd": 25_000_000,
     "transfer_fee_bps": 30,
     "metadata": {"green_certified": True}},
    {"symbol": "RWAISE-NYC-RE-A", "name": "NYC Office REIT — Series A",
     "asset_class": "real_estate",
     "spv_name": "RWAISE NYC Office A LLC",
     "spv_jurisdiction": "US-DE",
     "valuation_usd": 48_000_000,
     "transfer_fee_bps": 50,
     "metadata": {"property": "Manhattan tower, 280k sqft"}},
    {"symbol": "RWAISE-TF-INV", "name": "Trade Finance Invoice Pool Q3",
     "asset_class": "debt",
     "spv_name": "RWAISE Trade Finance Q3 SPV",
     "spv_jurisdiction": "SG",
     "valuation_usd": 8_400_000,
     "transfer_fee_bps": 75,
     "metadata": {"avg_invoice_tenor_days": 60}},
    {"symbol": "RWAISE-AU-1KG", "name": "Allocated Gold — 1kg bars",
     "asset_class": "commodities",
     "spv_name": "RWAISE Au Holdings Ltd",
     "spv_jurisdiction": "CH",
     "valuation_usd": 5_200_000,
     "transfer_fee_bps": 20,
     "metadata": {"vault": "ZRH-LBMA", "purity": "99.99%"}},
    {"symbol": "RWAISE-SOL-CA", "name": "California Solar Income 2030",
     "asset_class": "real_estate",
     "spv_name": "RWAISE Solar CA 2030 LLC",
     "spv_jurisdiction": "US-DE",
     "valuation_usd": 18_000_000,
     "transfer_fee_bps": 40,
     "metadata": {"capacity_mw": 75, "ppa_term_years": 20}},
    {"symbol": "RWAISE-MUSIC-2030", "name": "Music Royalties Catalogue 2030",
     "asset_class": "intellectual_property",
     "spv_name": "RWAISE Music Catalogue 2030 LLC",
     "spv_jurisdiction": "US-DE",
     "valuation_usd": 3_400_000,
     "transfer_fee_bps": 100,
     "metadata": {"catalogue_size": 1_240, "avg_track_age_years": 12}},
    {"symbol": "RWAISE-CARBON-1", "name": "Carbon Credits Vintage 2026",
     "asset_class": "carbon",
     "spv_name": "RWAISE Carbon 2026 SARL",
     "spv_jurisdiction": "LU",
     "valuation_usd": 2_100_000,
     "transfer_fee_bps": 60,
     "metadata": {"registry": "Verra", "vintage": 2026}},
)


def run(*, investors: int = 10, with_orderbook: bool = True) -> dict:
    """Seed 8 demo issuances + ``investors`` synthetic credentialed accounts."""
    from gopnik.rwaise.models import (
        MPTIssuance, MPTIssuanceStatus, MPTHolder, MPTHolderStatus,
    )
    from gopnik.models import db  # type: ignore[attr-defined]

    created_issuances: list[int] = []
    for entry in DEMO_ASSETS:
        existing = (db.session.query(MPTIssuance)
                    .filter(MPTIssuance.metadata_hash == entry["symbol"])
                    .first())
        if existing is not None:
            continue
        issuer = "rDEMO" + secrets.token_hex(15).upper()
        row = MPTIssuance(
            issuance_id=secrets.token_hex(8).upper(),
            issuer_account=issuer,
            asset_scale=2,
            transfer_fee=entry["transfer_fee_bps"],
            metadata_hash=entry["symbol"],
            metadata_json={
                "name": entry["name"],
                "symbol": entry["symbol"],
                "asset_class": entry["asset_class"],
                "spv": {
                    "name": entry["spv_name"],
                    "jurisdiction": entry["spv_jurisdiction"],
                },
                "valuation_usd": entry["valuation_usd"],
                **entry.get("metadata", {}),
            },
            status=MPTIssuanceStatus.validated,
        )
        db.session.add(row)
        db.session.flush()
        created_issuances.append(row.id)

        # Synthetic holders.
        for _ in range(min(investors, 20)):
            db.session.add(MPTHolder(
                issuance_id=row.id,
                holder_account="rDEMO" + secrets.token_hex(15).upper(),
                status=MPTHolderStatus.active,
                last_observed_balance=secrets.randbelow(50_000) + 100,
            ))

    db.session.commit()
    log.info("rwaise.seed_demo created %d issuances", len(created_issuances))
    return {"issuances": len(created_issuances)}


def reset() -> dict:
    """Drop everything we seeded."""
    from gopnik.rwaise.models import MPTIssuance, MPTHolder, MPTAuditEvent
    from gopnik.models import db  # type: ignore[attr-defined]
    syms = {e["symbol"] for e in DEMO_ASSETS}
    issuance_ids = [r.id for r in
                     db.session.query(MPTIssuance.id, MPTIssuance.metadata_hash)
                     .filter(MPTIssuance.metadata_hash.in_(syms)).all()]
    if issuance_ids:
        (db.session.query(MPTHolder)
         .filter(MPTHolder.issuance_id.in_(issuance_ids))
         .delete(synchronize_session=False))
        (db.session.query(MPTAuditEvent)
         .filter(MPTAuditEvent.issuance_id.in_(issuance_ids))
         .delete(synchronize_session=False))
        (db.session.query(MPTIssuance)
         .filter(MPTIssuance.id.in_(issuance_ids))
         .delete(synchronize_session=False))
    db.session.commit()
    return {"deleted_issuances": len(issuance_ids)}
