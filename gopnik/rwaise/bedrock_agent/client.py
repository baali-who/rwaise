"""AWS Bedrock Agent runtime client — wraps invoke_agent.

Two paths, selected at runtime by env:

  AWS_BEDROCK_AGENT_ID       set → real agent path
  AWS_BEDROCK_AGENT_ALIAS_ID set → real agent path
  RWAISE_BEDROCK_MOCK=1      → force mock (CI / hackathon-offline)
  (otherwise default to real)

Each invocation streams chunks back from invoke_agent; we collect them
and return the assembled text. The agent's action handler lives at
:mod:`gopnik.rwaise.bedrock_agent.actions`.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

log = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    text: str
    session_id: str
    citations: list = None
    raw: dict = None


def _use_mock() -> bool:
    if os.getenv("RWAISE_BEDROCK_MOCK", "").strip().lower() in ("1", "true", "yes"):
        return True
    return not os.getenv("AWS_BEDROCK_AGENT_ID")


def _bedrock_runtime():  # pragma: no cover  — real path
    try:
        from flask import current_app
        cached = current_app.extensions.get("rwaise_bedrock_agent_runtime") if current_app else None
        if cached is not None:
            return cached
    except Exception:
        cached = None
    import boto3
    region = os.getenv("AWS_REGION", "eu-north-1")
    client = boto3.client("bedrock-agent-runtime", region_name=region)
    try:
        from flask import current_app
        if current_app:
            current_app.extensions["rwaise_bedrock_agent_runtime"] = client
    except Exception:
        pass
    return client


def invoke(prompt: str, *, session_id: Optional[str] = None,
           user_id: Optional[int] = None) -> AgentResponse:
    """Invoke the agent. Returns the assembled text + session id."""
    sid = session_id or str(uuid.uuid4())
    if _use_mock():
        return AgentResponse(
            text=_mock(prompt),
            session_id=sid,
            citations=[],
            raw={"mock": True},
        )
    return _real_invoke(prompt, sid, user_id)


def _real_invoke(prompt: str, sid: str, user_id: Optional[int]) -> AgentResponse:  # pragma: no cover
    client = _bedrock_runtime()
    agent_id = os.getenv("AWS_BEDROCK_AGENT_ID", "")
    alias_id = os.getenv("AWS_BEDROCK_AGENT_ALIAS_ID", "TSTALIASID")
    resp = client.invoke_agent(
        agentId=agent_id,
        agentAliasId=alias_id,
        sessionId=sid,
        inputText=prompt,
        # Per-user attributes flow as session attributes — the action
        # handler reads these to enforce per-user agent caps.
        sessionState={
            "sessionAttributes": {
                "user_id": str(user_id) if user_id is not None else "",
            }
        } if user_id is not None else None,
    )
    chunks: list[str] = []
    citations: list = []
    for event in resp.get("completion", []):
        if "chunk" in event:
            data = event["chunk"].get("bytes", b"")
            if isinstance(data, bytes):
                chunks.append(data.decode("utf-8", errors="replace"))
            attribution = event["chunk"].get("attribution", {})
            for c in (attribution.get("citations") or []):
                citations.append(c)
    return AgentResponse(
        text="".join(chunks),
        session_id=sid,
        citations=citations,
        raw=None,
    )


def _mock(prompt: str) -> str:
    """Plausible Claude Sonnet 4.6 output for the in-product agent."""
    p = prompt.lower()
    if "balance" in p or "holding" in p:
        return ("Looking at your portfolio: you hold 1,250 RWAISE-TBILL-2030 "
                "(market value ≈ $1,287.50 at last index price), 80 RWAISE-NYC-RE-A "
                "(≈ $4,400), and 12,000 RWAISE-CARBON-1.\n\n"
                "Three of these are eligible for a redemption window opening "
                "Friday. Want me to draft the redemption tickets?")
    if "buy" in p or "purchase" in p or "acquire" in p:
        return ("Before I can place an order I need to confirm a few things:\n\n"
                "  1. The asset's listing — paste the URL or symbol.\n"
                "  2. Whether you've been credentialed for this asset class.\n"
                "  3. Whether the order should sit in the book or be immediate.\n\n"
                "Your daily-buy cap is $50,000 USD.")
    if "redemption" in p or "redeem" in p:
        return ("I can prepare a redemption ticket. The 4-eyes queue typically "
                "clears within 1 business day. The fiat payout will appear in "
                "the bank account on file, in the asset's stated payout currency.")
    return ("I'm the RWAiSE Trader. Ask me about your holdings, draft an order, "
            "request a redemption, or pull a price quote on any listed asset. "
            "I won't move funds without an explicit confirmation step.")
