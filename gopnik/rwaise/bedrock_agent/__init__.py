"""AWS Bedrock "RWAiSE Trader" agent package.

Two pieces:

  - :mod:`client`    — runtime invocation. Real (boto3) + mock paths.
  - :mod:`actions`   — Lambda action handler the agent calls into; binds
                        to the host Gopnik app at cold-start.

The agent's OpenAPI 3 schema lives at ``deployment/bedrock/openapi.yaml``
and the system prompt at ``deployment/bedrock/system_prompt.md`` —
both shipped in the standalone GitHub repo's ``deployment/bedrock/``
directory.
"""
from .client import invoke, AgentResponse

__all__ = ["invoke", "AgentResponse"]
