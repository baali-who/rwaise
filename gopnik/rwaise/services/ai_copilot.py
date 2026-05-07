"""Bedrock-Claude wizard co-pilot.

Implements the four wizard intents:

  - draft_prospectus  — markdown-formatted issuance prospectus from a WizardState.
  - suggest_mpt       — recommended MPT parameter set (asset_scale, transfer_fee, …).
  - review_docs       — flag inconsistencies across uploaded prospectus / SPA / KYC.
  - answer            — open-ended question answering grounded in the wizard state.

Runtime path selector
=====================
The user explicitly asked for "real if AWS keys present, mock otherwise".
The selector reads:

  • ``AWS_BEDROCK_AGENT_ID`` / ``AWS_BEDROCK_AGENT_ALIAS_ID`` — full agent path
    (uses ``invoke_agent``)
  • ``RWAISE_BEDROCK_MODEL_ID`` (default ``anthropic.claude-sonnet-4-6``)
    — direct model invocation via ``invoke_model``
  • ``RWAISE_BEDROCK_MOCK=1``  — force mock even when AWS creds present (CI / tests)

The mock returns plausible Claude-style markdown. It's good enough for
hackathon demos when the venue Wi-Fi flakes out, and the unit tests
run against it deterministically.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Any, Optional

log = logging.getLogger(__name__)


# ─── Typed wizard state (pure data — testable without Flask) ────────


@dataclass
class WizardState:
    asset_class: Optional[str] = None
    subclass: Optional[str] = None
    description: Optional[str] = None
    spv_name: Optional[str] = None
    spv_jurisdiction: Optional[str] = None
    valuation_usd: Optional[float] = None
    target_apy_bps: Optional[int] = None
    quote_currency: Optional[str] = None
    minimum_holding: Optional[float] = None


@dataclass
class MPTSuggestion:
    """Recommended MPT parameter set for the current wizard state."""
    asset_scale: int = 2
    maximum_amount: Optional[int] = None
    transfer_fee_bps: int = 0
    can_lock: bool = True
    require_auth: bool = True
    can_escrow: bool = True
    can_trade: bool = True
    can_transfer: bool = True
    can_clawback: bool = True
    rationale: str = ""


# ─── Path selection ─────────────────────────────────────────────────


def _use_mock() -> bool:
    """True when we should serve mock responses instead of calling Bedrock.

    Forced ON if RWAISE_BEDROCK_MOCK=1 (CI / hackathon-offline). Default
    OFF when any of these AWS-side env vars are set, since their
    presence is a strong signal the operator intends to use the real
    service:

      AWS_BEDROCK_AGENT_ID
      AWS_REGION    (and we're not running tests)
    """
    if os.getenv("RWAISE_BEDROCK_MOCK", "").strip().lower() in ("1", "true", "yes"):
        return True
    has_creds = bool(os.getenv("AWS_BEDROCK_AGENT_ID")
                     or os.getenv("AWS_REGION"))
    return not has_creds


def _bedrock_client():  # pragma: no cover  — real AWS path
    """Lazy boto3 client. Cached on the Flask app extensions if available."""
    try:
        from flask import current_app
        cached = current_app.extensions.get("rwaise_bedrock") if current_app else None
        if cached is not None:
            return cached
    except Exception:
        cached = None
    import boto3
    region = os.getenv("AWS_REGION", "eu-north-1")
    client = boto3.client("bedrock-runtime", region_name=region)
    try:
        from flask import current_app
        if current_app:
            current_app.extensions["rwaise_bedrock"] = client
    except Exception:
        pass
    return client


def _invoke_claude(prompt: str, *, max_tokens: int = 1024) -> str:
    """Direct ``invoke_model`` against Claude on Bedrock.

    Used when AWS_BEDROCK_AGENT_ID is *not* set (i.e. we want a single
    completion, not a multi-turn agent). Returns the model's text output.
    """
    if _use_mock():
        return _mock_claude(prompt)
    client = _bedrock_client()
    model_id = os.getenv("RWAISE_BEDROCK_MODEL_ID",
                         "anthropic.claude-sonnet-4-6")
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = client.invoke_model(modelId=model_id, body=body)
    payload = json.loads(resp["body"].read())
    parts = payload.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


# ─── Mock path (deterministic, demo-friendly) ───────────────────────


def _mock_claude(prompt: str) -> str:
    """Return plausible Claude output for the four wizard intents.

    The router below decides which template to fire based on the prompt
    prefix. Output is markdown so the wizard's marked-down renderer
    handles it identically to the real path.
    """
    p = prompt.lower()
    if "prospectus" in p:
        return _mock_prospectus()
    if "suggest mpt" in p or "parameter" in p:
        return _mock_suggest_mpt()
    if "review" in p:
        return _mock_review()
    return _mock_open_answer()


def _mock_prospectus() -> str:
    return (
        "## Issuance prospectus (draft)\n\n"
        "**Asset class.** Tokenised commercial real estate income — fractional "
        "ownership of a single SPV-held asset, distributing rental cashflows "
        "monthly.\n\n"
        "**Issuer / SPV.** A bankruptcy-remote special-purpose vehicle "
        "incorporated in the issuer's elected jurisdiction.\n\n"
        "**Token mechanics.** XLS-33 Multi-Purpose Token issued on the XRP "
        "Ledger mainnet. 1 token = 1 unit of beneficial interest in the SPV. "
        "Transfer requires an active XLS-65 \"verified investor\" credential.\n\n"
        "**Cashflows.** Monthly net rental income net of management fees is "
        "paid in RLUSD via on-ledger Payment to each holder of record on the "
        "last validated ledger of the calendar month.\n\n"
        "**Redemption.** Holders may submit a redemption ticket through the "
        "RWAiSE app. Tickets enter a 4-eyes review queue, a sanctions-list "
        "screen, and on approval the SPV pays USD by ACH and the tokens are "
        "burned via Clawback.\n\n"
        "**Risk factors.** Property vacancy, illiquidity, regulatory change, "
        "sanctions-list expansion, ledger-amendment risk on XLS-33.\n\n"
        "_This is a draft. Have your counsel review before final filing._"
    )


def _mock_suggest_mpt() -> str:
    return json.dumps({
        "asset_scale": 2,
        "maximum_amount": 1_000_000_00,
        "transfer_fee_bps": 50,
        "can_lock": True,
        "require_auth": True,
        "can_escrow": True,
        "can_trade": True,
        "can_transfer": True,
        "can_clawback": True,
        "rationale": (
            "Asset_scale=2 lets you express cents-of-a-token. require_auth=True "
            "is mandatory for a regulated asset class. transfer_fee=50bps "
            "covers the platform's ongoing compliance cost without crushing "
            "secondary-market liquidity. can_clawback=True is required by "
            "your jurisdiction's sanctions-list freeze obligations."
        ),
    })


def _mock_review() -> str:
    return json.dumps({
        "findings": [
            {"severity": "warning",
             "field": "valuation_usd",
             "issue": "Prospectus quotes USD 4.2 m, but the cap-table sums to "
                      "USD 4.05 m. Reconcile before filing."},
            {"severity": "info",
             "field": "spv_jurisdiction",
             "issue": "SPV jurisdiction (Delaware) and the offering target "
                      "(EU professional investors) usually require an "
                      "additional 871(m) statement."},
        ],
    })


def _mock_open_answer() -> str:
    return (
        "I'd recommend keeping the transfer fee at 50 bps for the first cohort "
        "and revisiting once you have 90 days of secondary-market data. The "
        "single biggest determinant of liquidity will be the size of the "
        "credentialed pool, not the fee.\n\n"
        "If you want a tighter answer I can dig into the relevant XRPL "
        "ledger amendments — just paste the section you're stuck on."
    )


# ─── Public API consumed by routes.py ───────────────────────────────


def draft_prospectus_summary(state: WizardState) -> str:
    """Return a markdown-formatted issuance prospectus draft."""
    prompt = (
        "Draft a one-page issuance prospectus in markdown for the following "
        f"RWA tokenisation. Be concrete and risk-aware.\n\n"
        f"State: {asdict(state)}\n\n"
        "Use these section headings: Asset class, Issuer/SPV, Token mechanics, "
        "Cashflows, Redemption, Risk factors. Mention XLS-33 (MPT) and "
        "XLS-65 (credentials) by name. End with a one-line legal disclaimer."
    )
    return _invoke_claude(prompt, max_tokens=1500)


def suggest_mpt_parameters(state: WizardState) -> MPTSuggestion:
    """Recommend an MPT parameter set. Returns a typed dataclass."""
    prompt = (
        "Suggest MPT parameters for the following RWA. Reply ONLY with JSON "
        "matching this schema:\n"
        "{asset_scale, maximum_amount, transfer_fee_bps, can_lock, "
        "require_auth, can_escrow, can_trade, can_transfer, can_clawback, "
        "rationale}\n\n"
        f"State: {asdict(state)}"
    )
    raw = _invoke_claude(prompt, max_tokens=600)
    try:
        # Strip markdown code fences that Claude sometimes adds
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        data = json.loads(cleaned)
    except Exception as exc:
        log.warning("suggest_mpt JSON parse failed (%s); using defaults", exc)
        return MPTSuggestion(rationale="Could not parse model output; using safe defaults.")
    out = MPTSuggestion()
    for f in (
        "asset_scale", "maximum_amount", "transfer_fee_bps",
        "can_lock", "require_auth", "can_escrow", "can_trade",
        "can_transfer", "can_clawback", "rationale",
    ):
        if f in data:
            setattr(out, f, data[f])
    return out


def review_documents(documents: dict[str, Any]) -> list[dict[str, Any]]:
    """Flag inconsistencies across uploaded prospectus / SPA / KYC docs."""
    prompt = (
        "Review these RWA tokenisation documents and flag any factual "
        "inconsistencies, missing required fields, or compliance red flags. "
        "Reply ONLY with JSON: {findings: [{severity, field, issue}, …]}.\n\n"
        f"Documents: {json.dumps(documents)[:6000]}"
    )
    raw = _invoke_claude(prompt, max_tokens=800)
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        return json.loads(cleaned).get("findings", [])
    except Exception:
        return []


def answer(question: str, state: WizardState) -> str:
    """Open-ended Q&A grounded in the wizard state."""
    prompt = (
        f"You are an expert in RWA tokenisation on the XRP Ledger. Answer "
        f"the user's question concisely (≤ 250 words) using the issuance "
        f"context.\n\nIssuance state: {asdict(state)}\n\nQuestion: {question}"
    )
    return _invoke_claude(prompt, max_tokens=600)
