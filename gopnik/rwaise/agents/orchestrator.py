"""Bedrock Converse-API orchestrator.

Drives the tool-use loop:

  1. Send the user message + tool schema to Bedrock Claude.
  2. If Claude returns a ``tool_use`` block, run that tool with
     :func:`gopnik.rwaise.agents.tools.dispatch`, append the result as
     a ``tool_result`` content block, loop.
  3. When Claude returns a final ``text`` block (no more tool_use),
     return it.

Two paths
=========

  * **Real** — uses ``boto3.client('bedrock-runtime').converse``.
    Model id from ``RWAISE_BEDROCK_MODEL_ID`` (defaults to
    ``anthropic.claude-sonnet-4-6``).
  * **Mock** — when ``RWAISE_BEDROCK_MOCK=1`` or no AWS creds, runs a
    rule-based mini-agent that fans out a few canned tool calls and
    returns a templated final answer. Lets the demo work offline.

Safety
======
  * Hard cap of 6 tool-use turns per user message — no infinite loops.
  * Per-session ``SpendingCap`` shared with every Payer.
  * Every tool call audit-logged in :class:`ToolContext.audit`.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from .tools import (
    TOOL_SCHEMAS, ToolContext, AuditEvent, dispatch,
)
from .wallet_payer import SpendingCap

log = logging.getLogger(__name__)

_MAX_TURNS = 6


SYSTEM_PROMPT = """\
You are RWAiSE Trader, an AI assistant inside the Gopnik wallet that
helps the user reason about crypto markets and (with explicit
permission) propose XRPL transactions.

You have six tools:

  - get_spot_price            — free CoinDesk spot price
  - get_historical_close      — free CoinDesk daily history
  - get_premium_signal        — paid CoinDesk signal (you spend a few
                                 drops via x402; subject to caps)
  - list_my_wallets           — read the user's Gopnik wallets
  - propose_transaction       — build (NOT broadcast) an XRPL Payment
  - confirm_transaction       — broadcast a previously-proposed tx,
                                 requires the confirmation_code the
                                 user reads back

Hard rules:
  * NEVER call confirm_transaction without first calling
    propose_transaction in the same conversation AND getting an
    explicit confirmation code from the user (read back as a number).
  * NEVER move funds without spelling out the from / to / amount
    plainly to the user first.
  * Stay under the per-session spending cap; if a premium call would
    exceed it, fall back to free data and tell the user.
  * Be terse. Numbers and tx hashes are more useful than prose.

Style:
  * Lead with the conclusion / recommendation; back it with data.
  * Render dollar amounts to 2 decimals, prices to the appropriate
    precision (BTC: nearest dollar, XRP: 4 decimals).
  * Always cite the data source ('CoinDesk free' / 'CoinDesk premium
    via x402') so the user knows what's load-bearing.
"""


@dataclass
class AgentReply:
    text: str
    audit: list[dict[str, Any]]
    cost_usd: float
    turns: int
    tools_used: list[str]
    pending_tickets: dict[str, Any] = None  # carried back to the caller

    def __post_init__(self):
        if self.pending_tickets is None:
            self.pending_tickets = {}


# ─── Public entrypoint ───────────────────────────────────────────────


def run(user, message: str, *,
        cap_usd: Optional[float] = None,
        history: Optional[list[dict[str, Any]]] = None,
        pending_tickets: Optional[dict[str, Any]] = None) -> AgentReply:
    """Run one user-message → agent-reply turn.

    Parameters
    ----------
    user : authenticated user object (Flask-Login current_user)
    message : the user's text
    cap_usd : per-session $ cap for x402 micropayments
    history : prior conversation in Bedrock Converse format
    pending_tickets : dict of ticket_id → ticket from the previous
        turn. Lets the user confirm a transaction in a follow-up
        message ("confirm tkt_abc123 654321").
    """
    cap = SpendingCap(max_session_usd=cap_usd or _default_cap_usd())
    ctx = ToolContext(user=user, cap=cap,
                       pending_tickets=dict(pending_tickets or {}))

    if _use_mock():
        text, tools_used = _run_mock(user, message, ctx)
        reply = AgentReply(
            text=text,
            audit=[e.to_jsonable() for e in ctx.audit],
            cost_usd=cap.spent_usd,
            turns=1, tools_used=tools_used,
        )
        reply.pending_tickets = dict(ctx.pending_tickets)
        return reply

    reply = _run_real(user, message, ctx, history or [])
    reply.pending_tickets = dict(ctx.pending_tickets)
    return reply


# ─── Mock path ───────────────────────────────────────────────────────


def _run_mock(user, message: str, ctx: ToolContext) -> tuple[str, list[str]]:
    """Rule-based stand-in. Demo-friendly; works offline.

    iter-19 — emits real Markdown so the renderer can format
    headers, lists, code, bold. Uses wallet aliases from the DB
    instead of bare addresses.
    """
    msg = (message or "").lower()
    used: list[str] = []
    sections: list[str] = []

    # ── Detect a target symbol anywhere in the message ────────────
    symbol_aliases = {
        "btc": "BTC", "bitcoin": "BTC",
        "eth": "ETH", "ethereum": "ETH",
        "xrp": "XRP", "ripple": "XRP",
        "sol": "SOL", "solana": "SOL",
        "rlusd": "RLUSD", "usdc": "USDC",
    }
    found_syms: list[str] = []
    for word in msg.replace(",", " ").replace("?", " ").split():
        sym = symbol_aliases.get(word.strip("$.!:;()"))
        if sym and sym not in found_syms:
            found_syms.append(sym)

    # ── Intent: spot price / history ──────────────────────────────
    wants_history = any(w in msg for w in ("history", "historical",
                                             "7-day", "7 day", "week",
                                             "last 7", "past week"))
    wants_signal  = any(w in msg for w in ("buy", "sell", "hold", "signal",
                                             "recommend", "should i",
                                             "premium"))
    wants_wallets = any(w in msg for w in ("wallet", "wallets", "portfolio",
                                             "balance", "holding", "holdings",
                                             "accounts"))
    wants_draft   = any(w in msg for w in ("draft", "send", "transfer",
                                             "transaction", "pay"))
    wants_demo    = any(w in msg for w in ("hello", "hi ", "hey", "what can",
                                             "help", "start"))

    # ── Spot prices ───────────────────────────────────────────────
    if found_syms or "price" in msg:
        syms = found_syms or ["BTC", "XRP"]
        rows = []
        for sym in syms[:4]:
            out = dispatch("get_spot_price", {"symbol": sym}, ctx)
            used.append("get_spot_price")
            price = out.get("price_usd")
            src = out.get("source", "—")
            if price is None:
                rows.append(f"| {sym} | _unavailable_ | {src} |")
            else:
                rows.append(f"| **{sym}** | ${price:,.4f} | _{src}_ |")
        sections.append(
            "### Spot prices\n\n"
            "| Symbol | USD | Source |\n"
            "|---|---|---|\n" + "\n".join(rows)
        )

    # ── Premium x402 signal ───────────────────────────────────────
    if wants_signal and (found_syms or "all" in msg):
        sym = (found_syms or ["BTC"])[0]
        out2 = dispatch("get_premium_signal",
                          {"symbol": sym, "max_pay_usd": 0.05}, ctx)
        used.append("get_premium_signal")
        direction = (out2.get("direction") or "hold").upper()
        conf = float(out2.get("confidence") or 0.0)
        cost = float(out2.get("cost_usd_paid") or 0.0)
        tx_hash = out2.get("paid_tx_hash") or "—"
        rationale = out2.get("rationale") or ""
        sections.append(
            f"### Premium signal · {sym}\n\n"
            f"**Recommendation:** {direction}  ·  **Confidence:** {conf:.0%}\n\n"
            f"> {rationale}\n\n"
            f"_Paid_ `${cost:.4f}` _via x402 · tx_ `{tx_hash[:16]}…`"
        )

    # ── 7-day history ─────────────────────────────────────────────
    if wants_history:
        sym = (found_syms or ["BTC"])[0]
        out = dispatch("get_historical_close",
                         {"symbol": sym, "days": 7}, ctx)
        used.append("get_historical_close")
        pts = out.get("points", [])
        rows = "\n".join(
            f"| {p['day']} | ${float(p['close_usd']):,.2f} |" for p in pts
        )
        sections.append(
            f"### {sym} · last {len(pts)} days\n\n"
            "| Day | Close (USD) |\n|---|---|\n" + rows
        )

    # ── Wallets ───────────────────────────────────────────────────
    if wants_wallets or wants_draft:
        out = dispatch("list_my_wallets", {}, ctx)
        used.append("list_my_wallets")
        wallets = out.get("wallets", []) if isinstance(out, dict) else []
        if wallets:
            rows = []
            for w in wallets[:8]:
                alias = w.get("alias") or "(no alias)"
                addr  = w.get("address") or "?"
                short = f"`{addr[:8]}…{addr[-6:]}`"
                badges = []
                if w.get("is_master"): badges.append("master")
                if w.get("can_sign"):  badges.append("can sign")
                badge_str = " · ".join(badges) if badges else "view-only"
                rows.append(f"- **{alias}** {short} — _{badge_str}_")
            extra = ""
            if len(wallets) > 8:
                extra = f"\n\n_…and {len(wallets) - 8} more_"
            sections.append(
                f"### Your wallets ({len(wallets)})\n\n" +
                "\n".join(rows) + extra
            )
        else:
            sections.append(
                "### Wallets\n\n"
                "You don't have any wallets imported yet. "
                "Add one in **Wallets → Import**."
            )

    # ── Confirm a previously-proposed transaction ────────────────
    # Accepts both:  "confirm tkt_abc123 654321"
    #          and:  "654321"     (6-digit code by itself)
    import re as _re
    confirm_match = (
        _re.match(r"^\s*confirm\s+(tkt_\w+)\s+(\d{6})\s*$", msg, flags=_re.I) or
        _re.match(r"^\s*(tkt_\w+)\s+(\d{6})\s*$", msg, flags=_re.I)
    )
    bare_code = _re.match(r"^\s*(\d{6})\s*$", msg)
    if confirm_match or (bare_code and ctx.pending_tickets):
        if confirm_match:
            tid = confirm_match.group(1)
            code = confirm_match.group(2)
        else:
            # Newest ticket wins.
            tid = sorted(ctx.pending_tickets.keys())[-1]
            code = bare_code.group(1)
        out = dispatch("confirm_transaction",
                         {"ticket_id": tid, "confirmation_code": code}, ctx)
        used.append("confirm_transaction")
        if "error" in out:
            sections.append(
                f"### ❌ Transaction failed\n\n"
                f"_{out.get('error')}_\n\n"
                f"Use `propose_transaction` again to get a fresh ticket.")
        else:
            tx_hash = out.get("tx_hash", "")
            sections.append(
                f"### ✅ Transaction broadcast\n\n"
                f"**Tx hash:** `{tx_hash}`\n\n"
                f"**From:** `{out.get('from')}`\n"
                f"**To:** `{out.get('to')}`\n"
                f"**Amount:** {out.get('amount')}\n\n"
                f"[View on XRPL Explorer](https://livenet.xrpl.org/transactions/{tx_hash})")
        # Done — short-circuit so we don't also propose a fresh transfer.
        return ("\n\n".join(sections), used)

    # ── Draft a new transfer (calls propose_transaction for real) ─
    if wants_draft:
        amt_match = _re.search(
            r"(\d+(?:\.\d+)?)\s*(xrp|gopnik|btc|eth|usdc|rlusd)",
            msg, flags=_re.I)
        addr_match = _re.search(
            r"r[1-9A-HJ-NP-Za-km-z]{24,34}", message or "")
        src_match = _re.search(
            r"from\s+(?:my\s+)?([\w\-]+|r[1-9A-HJ-NP-Za-km-z]{24,34})",
            msg, flags=_re.I)

        amt = amt_match.group(1) if amt_match else None
        cur = (amt_match.group(2).upper() if amt_match else "XRP")
        dst = addr_match.group(0) if addr_match else None
        src_hint = (src_match.group(1).lower() if src_match else "master")

        # Resolve source wallet from the user's actual wallet list.
        wallets_out = dispatch("list_my_wallets", {}, ctx)
        used.append("list_my_wallets")
        wallets = (wallets_out.get("wallets") or []) if isinstance(
            wallets_out, dict) else []

        src_wallet = None
        for w in wallets:
            alias = (w.get("alias") or "").lower()
            addr  = w.get("address") or ""
            if src_hint == addr.lower() or src_hint in alias.lower():
                src_wallet = w
                break
        if src_wallet is None and src_hint == "master":
            src_wallet = next((w for w in wallets if w.get("is_master")), None)
        if src_wallet is None and wallets:
            src_wallet = wallets[0]

        if not amt or not dst or not src_wallet:
            missing = []
            if not amt:        missing.append("amount + currency")
            if not dst:        missing.append("destination address")
            if not src_wallet: missing.append("source wallet")
            sections.append(
                f"### Draft transfer · need more info\n\n"
                f"I can't build this yet. Missing: **{', '.join(missing)}**.\n\n"
                f"Try: `Send 2 XRP from my master wallet to "
                f"rExampleDestinationAddressHere000000`")
        else:
            from_addr = src_wallet["address"]
            propose = dispatch("propose_transaction", {
                "from_address": from_addr,
                "to_address":   dst,
                "amount":       str(amt),
                "currency":     cur,
                "memo":         "rwaise.agent.draft",
            }, ctx)
            used.append("propose_transaction")
            if "error" in propose:
                sections.append(
                    f"### ❌ Couldn't draft\n\n_{propose.get('error')}_")
            else:
                code = propose.get("confirmation_code")
                tid  = propose.get("ticket_id")
                alias = src_wallet.get("alias") or "(no alias)"
                sections.append(
                    f"### ✋ Confirm to broadcast\n\n"
                    f"| Field | Value |\n|---|---|\n"
                    f"| **From** | {alias} `{from_addr[:10]}…{from_addr[-6:]}` |\n"
                    f"| **To**   | `{dst[:10]}…{dst[-6:]}` |\n"
                    f"| **Amount** | **{amt} {cur}** |\n"
                    f"| **Confirmation code** | `{code}` |\n"
                    f"| **Ticket ID** | `{tid}` |\n\n"
                    f"To broadcast on the XRPL, **type the 6-digit code** "
                    f"`{code}` and send. (Or paste `confirm {tid} {code}`.) "
                    f"The ticket expires in 5 minutes.")

    # ── Help / fallback ───────────────────────────────────────────
    if not sections or wants_demo:
        sections.insert(0,
            "### What I can do here\n\n"
            "- **\"What's the price of XRP?\"** — free spot from CoinDesk.\n"
            "- **\"Should I buy BTC?\"** — buys a premium signal via **x402** "
            "for ~$0.025 from your wallet.\n"
            "- **\"List my wallets\"** — pulls them from Gopnik, with aliases.\n"
            "- **\"Send 5 XRP from my master to r…\"** — drafts the tx, asks "
            "you to confirm with a 6-digit code.\n\n"
            "_Running in **mock mode**. Set `AWS_ACCESS_KEY_ID` to use the "
            "real Bedrock Claude Sonnet 4.6._"
        )

    return ("\n\n".join(sections), used)


# ─── Real path (AWS Bedrock Converse) ────────────────────────────────


def _run_real(user, message: str, ctx: ToolContext,
              history: list[dict[str, Any]]) -> AgentReply:        # pragma: no cover
    import boto3

    region = os.getenv("AWS_REGION", "eu-north-1")
    model_id = os.getenv("RWAISE_BEDROCK_MODEL_ID",
                          "anthropic.claude-sonnet-4-6")
    client = boto3.client("bedrock-runtime", region_name=region)

    # Bedrock Converse format: each message has role + content blocks.
    messages: list[dict[str, Any]] = list(history)
    messages.append({"role": "user", "content": [{"text": message}]})

    tools_used: list[str] = []
    final_text = ""

    for turn in range(_MAX_TURNS):
        resp = client.converse(
            modelId=model_id,
            messages=messages,
            system=[{"text": SYSTEM_PROMPT}],
            toolConfig={
                "tools": [{"toolSpec": {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": {"json": t["input_schema"]},
                }} for t in TOOL_SCHEMAS],
            },
            inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
        )

        out = resp.get("output", {}).get("message", {})
        messages.append(out)

        # Collect any tool_use blocks; if there are none, we're done.
        tool_uses = [c for c in out.get("content", []) if "toolUse" in c]
        if not tool_uses:
            final_text = "\n".join(c.get("text", "") for c in out.get("content", [])
                                    if "text" in c).strip()
            break

        tool_result_content: list[dict[str, Any]] = []
        for c in tool_uses:
            tu = c["toolUse"]
            name = tu["name"]
            tools_used.append(name)
            result = dispatch(name, tu.get("input", {}), ctx)
            tool_result_content.append({
                "toolResult": {
                    "toolUseId": tu["toolUseId"],
                    "content": [{"json": result}],
                }
            })
        messages.append({"role": "user", "content": tool_result_content})

    return AgentReply(
        text=final_text or "(model returned no text)",
        audit=[e.to_jsonable() for e in ctx.audit],
        cost_usd=ctx.cap.spent_usd,
        turns=min(turn + 1, _MAX_TURNS),
        tools_used=tools_used,
    )


# ─── Helpers ─────────────────────────────────────────────────────────


def _use_mock() -> bool:
    """Return True iff we should use the rule-based agent rather than
    real Bedrock Claude.

    Rule-based fires when:
      1. ``RWAISE_BEDROCK_MOCK=1`` is set (explicit override), OR
      2. AWS credentials are missing (no point trying to call Bedrock), OR
      3. ``boto3`` isn't installed in the container.

    Case (3) is the single most common failure cause in production —
    AWS keys are set (e.g. for Secrets Manager) but boto3 wasn't added
    to requirements.txt. Without this guard the orchestrator tries to
    ``import boto3`` and crashes with a 500.
    """
    if os.getenv("RWAISE_BEDROCK_MOCK", "").strip().lower() in ("1", "true", "yes"):
        return True
    if not os.getenv("AWS_ACCESS_KEY_ID"):
        return True
    try:
        import boto3  # noqa: F401
    except ImportError:
        log.warning(
            "AWS_ACCESS_KEY_ID is set but boto3 is not installed in the "
            "container — falling back to the rule-based agent. Add "
            "'boto3>=1.34' to requirements.txt and rebuild to enable "
            "Bedrock Claude.")
        return True
    return False


def _default_cap_usd() -> float:
    try:
        return float(os.getenv("RWAISE_AGENT_SESSION_CAP_USD", "1.00"))
    except ValueError:
        return 1.00
