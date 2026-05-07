"""Bedrock tool definitions for the RWAiSE Trader agent.

The orchestrator passes the JSON Schema below to the Bedrock Converse
API; Claude Sonnet 4.6 picks tools to call, we execute them, and
hand the result back. Each ``run_*`` function:

  * Validates inputs.
  * Records an :class:`AuditEvent` for the conversation log.
  * Returns a JSON-serialisable dict that goes back into the model.

Every tool is deliberately read-only or proposal-only. The single
write-tool ``confirm_transaction`` requires a fresh user
confirmation passed back as a numeric code — Claude can never
broadcast on its own initiative.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger(__name__)


# ─── Tool schema (JSON Schema, sent to Bedrock) ──────────────────────


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_spot_price",
        "description": "Fetch the live USD spot price for a crypto asset from "
                        "CoinDesk's free BPI feed. Use this for free, "
                        "unauthenticated lookups. Cost: $0.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string",
                            "description": "Symbol like 'BTC', 'ETH', 'XRP'."},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_historical_close",
        "description": "Daily close prices for the last N calendar days. Free.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "days":   {"type": "integer", "minimum": 1, "maximum": 90},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_premium_signal",
        "description": "Fetch CoinDesk's premium buy/sell/hold signal for a "
                        "symbol. Costs a few drops via x402 (the agent "
                        "pays the facilitator on the user's behalf, "
                        "subject to per-call and per-session caps).",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":      {"type": "string"},
                "max_pay_usd": {"type": "number", "minimum": 0,
                                 "default": 0.05},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "list_my_wallets",
        "description": "List XRPL wallets the current user has imported into "
                        "Gopnik. Returns address, alias, and whether the "
                        "wallet has a signing seed available.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "propose_transaction",
        "description": "Build (but do NOT broadcast) an XRPL Payment from "
                        "one of the user's wallets. Returns a "
                        "``ticket_id`` and a numeric ``confirmation_code`` "
                        "the user must read back to authorise. The "
                        "transaction is held in memory for 5 minutes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {"type": "string"},
                "to_address":   {"type": "string"},
                "amount":       {"type": "string",
                                  "description": "Amount as string. Drops "
                                  "for XRP, decimal for IOUs."},
                "currency":     {"type": "string", "default": "XRP"},
                "issuer":       {"type": "string",
                                  "description": "Required when currency != XRP."},
                "memo":         {"type": "string"},
            },
            "required": ["from_address", "to_address", "amount", "currency"],
        },
    },
    {
        "name": "confirm_transaction",
        "description": "Confirm and broadcast a previously-proposed transaction. "
                        "Requires the ticket_id returned by "
                        "propose_transaction AND the numeric "
                        "confirmation_code the user reads from chat. "
                        "Returns the on-ledger tx hash.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id":         {"type": "string"},
                "confirmation_code": {"type": "string"},
            },
            "required": ["ticket_id", "confirmation_code"],
        },
    },
]


# ─── Audit ───────────────────────────────────────────────────────────


@dataclass
class AuditEvent:
    when: datetime
    tool: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    cost_usd: float = 0.0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "when": self.when.isoformat(),
            "tool": self.tool,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "cost_usd": self.cost_usd,
        }


@dataclass
class ToolContext:
    """Per-conversation state shared by every tool call."""
    user: Any
    cap: Any                                                          # SpendingCap
    pending_tickets: dict[str, dict[str, Any]] = field(default_factory=dict)
    audit: list[AuditEvent] = field(default_factory=list)


# ─── Dispatcher ──────────────────────────────────────────────────────


def dispatch(name: str, inputs: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Route a Bedrock tool call to its handler."""
    handler = {
        "get_spot_price":       _t_spot_price,
        "get_historical_close": _t_historical_close,
        "get_premium_signal":   _t_premium_signal,
        "list_my_wallets":      _t_list_my_wallets,
        "propose_transaction":  _t_propose_transaction,
        "confirm_transaction":  _t_confirm_transaction,
    }.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}

    try:
        out = handler(inputs or {}, ctx)
    except PermissionError as e:
        out = {"error": str(e), "kind": "permission"}
    except Exception as e:                                            # pragma: no cover
        log.exception("Tool %s crashed: %s", name, e)
        out = {"error": f"{type(e).__name__}: {e}", "kind": "internal"}

    cost = float(out.get("cost_usd", 0.0)) if isinstance(out, dict) else 0.0
    ctx.audit.append(AuditEvent(
        when=datetime.utcnow(), tool=name,
        inputs=inputs, outputs=out, cost_usd=cost,
    ))
    return out


# ─── Tool handlers ───────────────────────────────────────────────────


def _t_spot_price(inputs: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from . import coindesk
    sp = coindesk.spot_price(inputs.get("symbol", "BTC"))
    return sp.to_jsonable()


def _t_historical_close(inputs: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from . import coindesk
    pts = coindesk.historical(inputs.get("symbol", "BTC"),
                                int(inputs.get("days", 7)))
    return {"points": [p.to_jsonable() for p in pts],
            "count": len(pts)}


def _t_premium_signal(inputs: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Buy a premium signal. In mock mode, never touches the DB or XRPL —
    short-circuits so a broken wallet table can't break the agent."""
    from . import coindesk
    from .wallet_payer import WalletUserPayer, _force_mock as _is_mock_mode

    sym = inputs.get("symbol", "BTC")
    max_usd = float(inputs.get("max_pay_usd", 0.05))

    # Fast-path: mock mode skips the wallet resolution entirely.
    if _is_mock_mode():
        sig = coindesk.premium_signal(sym, payer=_NullPayer(), max_pay_usd=max_usd)
        out = sig.to_jsonable()
        out["note"] = "mock mode — no on-ledger payment"
        out["cost_usd"] = sig.cost_usd_paid
        return out

    # Live path: try to resolve a signing wallet. If anything goes wrong
    # (mapper config, missing seed, etc.) we still return a mocked
    # signal rather than failing the whole agent turn.
    wallet = None
    try:
        wallet = _resolve_wallet(ctx)
    except Exception:
        wallet = None

    if wallet is None:
        sig = coindesk.premium_signal(sym, payer=_NullPayer(), max_pay_usd=max_usd)
        out = sig.to_jsonable()
        out["note"] = "no signing wallet available — premium signal returned in mock mode"
        out["cost_usd"] = sig.cost_usd_paid
        return out

    payer = WalletUserPayer(wallet_user=wallet, cap=ctx.cap,
                              max_pay_usd_per_call=max_usd)
    try:
        sig = coindesk.premium_signal(sym, payer=payer, max_pay_usd=max_usd)
    except Exception as e:
        # Network down or facilitator broken: degrade to mock + tell caller.
        log.warning("premium_signal payment path failed (%s) — degrading to mock", e)
        sig = coindesk.premium_signal(sym, payer=_NullPayer(), max_pay_usd=max_usd)
        out = sig.to_jsonable()
        out["note"] = f"x402 payment path failed: {e}"
        out["cost_usd"] = sig.cost_usd_paid
        return out

    out = sig.to_jsonable()
    out["session_remaining_usd"] = ctx.cap.remaining()
    out["cost_usd"] = sig.cost_usd_paid
    return out


def _t_list_my_wallets(inputs: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from gopnik.models import db, WalletUser                          # type: ignore[attr-defined]
    rows = (db.session.query(WalletUser)
            .filter(WalletUser.imported_by_user_id == ctx.user.id)
            .order_by(WalletUser.id.asc()).all())
    return {
        "wallets": [
            {
                "address": w.address,
                "alias":   getattr(w, "alias", None) or "",
                "is_master": bool(getattr(w, "is_master", False)),
                "can_sign":  bool(getattr(w, "seed_encrypted", None)),
            } for w in rows
        ],
        "count": len(rows),
    }


def _t_propose_transaction(inputs: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Build a ticket. NEVER broadcasts — confirm_transaction does that."""
    from gopnik.models import db, WalletUser                          # type: ignore[attr-defined]
    src = (db.session.query(WalletUser)
           .filter(WalletUser.imported_by_user_id == ctx.user.id,
                   WalletUser.address == inputs.get("from_address"))
           .first())
    if src is None:
        return {"error": "from_address is not one of your wallets",
                "kind": "validation"}
    code = f"{secrets.randbelow(900000) + 100000}"  # 6-digit
    ticket_id = f"tkt_{secrets.token_hex(6)}"
    ctx.pending_tickets[ticket_id] = {
        "from_address": inputs["from_address"],
        "to_address":   inputs["to_address"],
        "amount":       inputs["amount"],
        "currency":     inputs.get("currency", "XRP"),
        "issuer":       inputs.get("issuer"),
        "memo":         inputs.get("memo", ""),
        "code":         code,
        "created_at":   datetime.utcnow().isoformat(),
    }
    # Show the user a preview alongside the code.
    return {
        "ticket_id": ticket_id,
        "confirmation_code": code,
        "preview": {
            "from": inputs["from_address"],
            "to":   inputs["to_address"],
            "amount": f"{inputs['amount']} {inputs.get('currency','XRP')}",
            "memo": inputs.get("memo", ""),
        },
        "instructions":
            "Show the preview to the user and ask them to type the "
            "confirmation_code back. Then call confirm_transaction.",
    }


def _t_confirm_transaction(inputs: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    ticket = ctx.pending_tickets.get(inputs.get("ticket_id", ""))
    if not ticket:
        return {"error": "Unknown or expired ticket_id", "kind": "validation"}
    if str(inputs.get("confirmation_code", "")).strip() != ticket["code"]:
        return {"error": "Confirmation code does not match", "kind": "permission"}

    from gopnik.models import db, WalletUser                          # type: ignore[attr-defined]
    from .wallet_payer import WalletUserPayer
    src = (db.session.query(WalletUser)
           .filter(WalletUser.imported_by_user_id == ctx.user.id,
                   WalletUser.address == ticket["from_address"])
           .first())
    if src is None:
        return {"error": "Source wallet missing", "kind": "validation"}
    payer = WalletUserPayer(wallet_user=src, cap=ctx.cap,
                              max_pay_usd_per_call=10.00)  # explicit confirm = bigger cap
    try:
        tx_hash = payer.pay(
            destination=ticket["to_address"],
            amount=ticket["amount"],
            currency=ticket["currency"],
            issuer=ticket.get("issuer"),
            memo=ticket.get("memo") or "rwaise.agent.confirm",
        )
    except PermissionError as e:
        return {"error": str(e), "kind": "permission"}

    # Single-use: remove the ticket.
    ctx.pending_tickets.pop(inputs["ticket_id"], None)
    return {
        "tx_hash": tx_hash,
        "from": ticket["from_address"],
        "to":   ticket["to_address"],
        "amount": f"{ticket['amount']} {ticket['currency']}",
    }


# ─── Helpers ─────────────────────────────────────────────────────────


def _resolve_wallet(ctx: ToolContext):
    """Pick the user's master wallet (or first wallet) for x402 micropayments."""
    from gopnik.models import db, WalletUser                          # type: ignore[attr-defined]
    q = (db.session.query(WalletUser)
         .filter(WalletUser.imported_by_user_id == ctx.user.id))
    return (q.filter(WalletUser.is_master.is_(True)).first()
            or q.order_by(WalletUser.id.asc()).first())


class _NullPayer:
    def pay(self, **kw) -> str:
        return "MOCK" + "0" * 60
