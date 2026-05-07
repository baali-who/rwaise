"""RWAiSE — Real-World Asset Issuance & Settlement Engine, as a Gopnik plugin.

The plugin is **opt-in** via the ``RWAISE_ENABLED`` environment variable.
When enabled, it adds these capabilities to the host Gopnik wallet
without modifying its existing surface:

  • A 9-step RWA tokenisation wizard (XLS-33 MPT, AI co-pilot, cap-table CSV,
    CloudHSM signing) at ``/rwaise/wizard``.
  • An XLS-65 layered credential issuer (RWAiSE itself issues a base
    "verified investor" credential plus optional jurisdiction /
    accreditation / asset-class attribute credentials).
  • An XLS-66-ready Permissioned-DEX order book for the marketplace,
    with an application-layer credential gate today and a
    one-env-var ledger-cutover seam at
    ``gopnik.rwaise.permissioned_dex.submit_offer``.
  • An MPT-aware escrow + 4-eyes redemption pipeline.
  • An ODL routing pilot using MPTs as bridge currencies.
  • The ``x402`` HTTP-402 payment middleware + on-ledger facilitator.
  • An AWS Bedrock "RWAiSE Trader" in-product agent (Claude Sonnet 4.6)
    plus an external-developer platform: OAuth 2.0 client-credentials,
    paid public API, developer portal.
  • A "RWAiSE" tab injected into Gopnik's top navigation.

Public API
==========

The plugin exposes exactly two helpers to the host application:

  ``register_plugin(app)``  — call from ``gopnik.create_app`` after the
                              core blueprints are registered.

  ``is_enabled()``          — boolean predicate; useful in templates,
                              CLI commands, or other plugins.

Both are no-ops when ``RWAISE_ENABLED`` is falsy. The plugin is
designed to be installed alongside Gopnik in the same Python package
tree (``gopnik/rwaise/``) and to share Gopnik's SQLAlchemy
``db``, ``User``, ``Wallet``, ``RWAAsset`` and login session.
"""
from .plugin import register_plugin, is_enabled

__all__ = ["register_plugin", "is_enabled"]

VERSION = "1.0.0"
