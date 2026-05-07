"""RWAiSE agents — Bedrock-Claude AI agents bound to the Gopnik wallet.

This package contains the **client-side** AI agents that consume APIs
(CoinDesk, x402-protected feeds), reason over the data with AWS
Bedrock Claude Sonnet 4.6, and propose / execute XRPL transactions
through the user's existing Gopnik wallets.

It does NOT serve agent APIs — that lives in
``gopnik.rwaise.bedrock_agent`` (the in-product co-pilot used by the
wizard) and ``gopnik.rwaise.api.public`` (the paid public API).

Modules
-------
  - :mod:`coindesk`        Typed CoinDesk REST adapter (free + premium).
  - :mod:`wallet_payer`    x402 ``Payer`` backed by a Gopnik WalletUser.
  - :mod:`tools`           The tool functions exposed to Bedrock.
  - :mod:`orchestrator`    Bedrock Converse-API tool-use loop + safety.
  - :mod:`chat_routes`     Flask blueprint mounting /rwaise/agent/api/chat.

Public surface
--------------
The Flask blueprint :data:`agent_chat_bp` is the only thing the host
application needs to register; ``plugin.py`` does this automatically
when the ``BEDROCK_AGENT`` sub-flag is on.
"""
from .chat_routes import agent_chat_bp

__all__ = ["agent_chat_bp"]
