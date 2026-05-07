"""Service-layer modules for the RWAiSE plugin.

Strategy
========
Gopnik's existing ``gopnik/rwa/services/`` (built in iters 3, 5, 13)
already implements MPT lifecycle, escrow, redemption, and the
pluggable HSM signer. This package thinly re-exports those where the
contract matches, and ships new modules for the genuinely RWAiSE-only
concerns.

Re-exported (via lazy ``__getattr__``) from gopnik.rwa.services
---------------------------------------------------------------
  - mpt          → gopnik.rwa.services.mpt
  - escrow       → gopnik.rwa.services.escrow
  - redemption   → gopnik.rwa.services.redemption
  - hsm_signer   → gopnik.rwa.services.hsm_signer
  - mpt_holder   → gopnik.rwa.services.mpt_holder

New (this plugin)
-----------------
  - ai_copilot         Bedrock-Claude wizard co-pilot (real + mock)
  - cap_table_loader   CSV cap-table parser + KYC/sanctions cross-check
  - credential_issuer  XLS-65 layered credential service
  - orderbook          XLS-66-ready order book + matcher
  - permissioned_dex   XLS-66 ledger cutover seam
  - odl_router         XLS-71 cross-currency routing pilot

Lazy ``__getattr__`` keeps cold-start fast — importing
``gopnik.rwaise.services`` does not pull all of xrpl-py into the
route-handler context.
"""
from __future__ import annotations

import importlib
from types import ModuleType

# Map: rwaise.services.<name>  →  gopnik.rwa.services.<target>
_REEXPORTS: dict[str, str] = {
    "mpt":         "gopnik.rwa.services.mpt",
    "escrow":      "gopnik.rwa.services.escrow",
    "redemption":  "gopnik.rwa.services.redemption",
    "hsm_signer":  "gopnik.rwa.services.hsm_signer",
    "mpt_holder":  "gopnik.rwa.services.mpt_holder",
}


def __getattr__(name: str) -> ModuleType:
    """Lazy resolver — re-exports first, then falls through to local modules."""
    if name in _REEXPORTS:
        return importlib.import_module(_REEXPORTS[name])
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ImportError as exc:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}: {exc}"
        ) from exc


def __dir__() -> list[str]:  # pragma: no cover
    return sorted({*_REEXPORTS,
                   "ai_copilot", "cap_table_loader", "credential_issuer",
                   "orderbook", "permissioned_dex", "odl_router"})


__all__ = list(_REEXPORTS) + [
    "ai_copilot", "cap_table_loader", "credential_issuer",
    "orderbook", "permissioned_dex", "odl_router",
]
