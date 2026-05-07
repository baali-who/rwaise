"""XLS-65 layered credential service.

XLS-65 introduces ledger-native verifiable credentials on the XRPL. The
RWAiSE platform is itself an issuer — it issues a "verified investor"
base credential plus optional attribute credentials that the
marketplace's cap-table-aware listings query at order time.

Layered credentials (the catalogue)
===================================

  RWAISE-VINV               base "verified investor" — KYC + sanctions
  RWAISE-JUR-US             jurisdiction marker — US person
  RWAISE-JUR-EU             jurisdiction marker — EU domiciled
  RWAISE-ACR-PROF           accreditation — professional / qualified
  RWAISE-ACR-INST           accreditation — institutional
  RWAISE-AC-RE              asset class — real estate
  RWAISE-AC-DEBT            asset class — debt instruments
  RWAISE-AC-CARBON          asset class — carbon credits

Each credential type is stored in :class:`CredentialAttributeType`. A
holder gets a row in :class:`CredentialGrant` with the issuance ID +
on-ledger tx hash. A marketplace listing references credentials via
:class:`MarketplaceCredentialRequirement`.

Until XLS-65 lands as a mainnet amendment, we emulate the ledger
semantics off-ledger: each credential is a long-lived MPT issuance
with require_auth=True and asset_scale=0 (1 token = 1 grant). Holding
1 token of the credential MPT == holding the credential.

Public API
----------
``bootstrap_catalogue()``  — idempotent: ensure the 8 base types exist
``grant(user_id, symbol, *, holder_wallet, reason)``
``revoke(grant_id, *, reason)``
``user_has(user_id, *, requires=[symbols])``
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Optional

log = logging.getLogger(__name__)

# Catalogue — ships with the platform; operators add custom symbols later.
CATALOGUE: tuple[dict, ...] = (
    {"symbol": "RWAISE-VINV", "name": "Verified investor",
     "kind": "base",
     "description": "Issued after KYC + sanctions screen.",
     "soulbound": True, "revocable": True},
    {"symbol": "RWAISE-JUR-US", "name": "Jurisdiction · United States",
     "kind": "jurisdiction",
     "description": "Holder is a US person.",
     "soulbound": True, "revocable": True},
    {"symbol": "RWAISE-JUR-EU", "name": "Jurisdiction · European Union",
     "kind": "jurisdiction",
     "description": "Holder is EU-domiciled.",
     "soulbound": True, "revocable": True},
    {"symbol": "RWAISE-ACR-PROF", "name": "Accreditation · Professional",
     "kind": "accreditation",
     "description": "Holder qualifies as a professional/qualified investor.",
     "soulbound": True, "revocable": True},
    {"symbol": "RWAISE-ACR-INST", "name": "Accreditation · Institutional",
     "kind": "accreditation",
     "description": "Holder qualifies as an institutional investor.",
     "soulbound": True, "revocable": True},
    {"symbol": "RWAISE-AC-RE", "name": "Asset class · Real estate",
     "kind": "asset_class",
     "description": "Cleared to hold tokenised real-estate assets.",
     "soulbound": True, "revocable": True},
    {"symbol": "RWAISE-AC-DEBT", "name": "Asset class · Debt instruments",
     "kind": "asset_class",
     "description": "Cleared to hold tokenised bonds and trade finance.",
     "soulbound": True, "revocable": True},
    {"symbol": "RWAISE-AC-CARBON", "name": "Asset class · Carbon credits",
     "kind": "asset_class",
     "description": "Cleared to hold tokenised carbon credits.",
     "soulbound": True, "revocable": True},
)


def bootstrap_catalogue() -> list:
    """Idempotently create catalogue rows. Called by ``flask rwaise bootstrap-credentials``."""
    from gopnik.rwaise.models import (
        CredentialAttributeType, CredentialAttributeKind,
    )
    from gopnik.models import db  # type: ignore[attr-defined]
    created = []
    for entry in CATALOGUE:
        existing = (db.session.query(CredentialAttributeType)
                    .filter_by(symbol=entry["symbol"]).first())
        if existing is not None:
            continue
        row = CredentialAttributeType(
            symbol=entry["symbol"],
            name=entry["name"],
            kind=CredentialAttributeKind(entry["kind"]),
            description=entry["description"],
            soulbound=entry["soulbound"],
            revocable=entry["revocable"],
        )
        db.session.add(row)
        created.append(row)
    db.session.commit()
    log.info("rwaise.credential_issuer bootstrapped %d types", len(created))
    return created


def grant(user_id: int, symbol: str, *, holder_wallet: str,
          reason: str = "") -> int:
    """Grant a credential to a user. Idempotent (returns existing ID if active)."""
    from gopnik.rwaise.models import (
        CredentialAttributeType, CredentialGrant,
    )
    from gopnik.models import db  # type: ignore[attr-defined]
    cred = (db.session.query(CredentialAttributeType)
            .filter_by(symbol=symbol).first())
    if cred is None:
        raise ValueError(f"unknown credential {symbol!r} — call bootstrap_catalogue() first")
    existing = (db.session.query(CredentialGrant)
                .filter_by(user_id=user_id, credential_type_id=cred.id,
                           revoked_at=None)
                .first())
    if existing is not None:
        return existing.id
    g = CredentialGrant(
        user_id=user_id, credential_type_id=cred.id,
        holder_wallet=holder_wallet, grant_reason=reason or None,
    )
    db.session.add(g)
    db.session.commit()
    return g.id


def revoke(grant_id: int, *, reason: str = "") -> None:
    from gopnik.rwaise.models import CredentialGrant
    from gopnik.models import db  # type: ignore[attr-defined]
    g = db.session.get(CredentialGrant, grant_id)
    if g is None or g.revoked_at is not None:
        return
    g.revoked_at = datetime.utcnow()
    g.revoke_reason = reason or None
    db.session.commit()


def user_has(user_id: int, *, requires: Iterable[str]) -> bool:
    """True iff the user has every credential in ``requires`` (active)."""
    from gopnik.rwaise.models import (
        CredentialAttributeType, CredentialGrant,
    )
    from gopnik.models import db  # type: ignore[attr-defined]
    needed = list(requires)
    if not needed:
        return True
    rows = (db.session.query(CredentialAttributeType.symbol)
            .join(CredentialGrant,
                  CredentialGrant.credential_type_id == CredentialAttributeType.id)
            .filter(CredentialGrant.user_id == user_id,
                    CredentialGrant.revoked_at.is_(None),
                    CredentialAttributeType.symbol.in_(needed))
            .all())
    held = {r[0] for r in rows}
    return all(s in held for s in needed)
