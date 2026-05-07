# RWAiSE Trader · AI Agent Deep-Dive

The agent is the centerpiece of RWAiSE — an AI assistant inside the Gopnik wallet that researches markets, pays for premium data over x402, and proposes XRPL transactions on the user's behalf. This doc explains how it works internally so you can extend it, debug it, or judge it.

---

## TL;DR

**AWS Bedrock Claude Sonnet 4.6** (Converse API + tool_use) drives a loop with **6 narrow tools**, real **Coinbase Developer Platform** market data, **x402** micropayments for premium signals, and a **propose-then-confirm** gate between the model and any real money movement. The user interacts via a Markdown-rendered chat UI at `/rwaise/agent`.

When AWS keys / `boto3` aren't installed, the agent gracefully falls back to a **rule-based engine** that uses the same 6 tools and the same real Coinbase data — so the agent is never broken, only "smarter or simpler".

---

## Files

```
gopnik/rwaise/agents/
├── orchestrator.py    Bedrock Converse-API tool-use loop + rule-based fallback
├── tools.py           6 tool schemas + dispatcher + audit + ToolContext
├── coindesk.py        Multi-source market data: Coinbase → CoinGecko → CoinDesk → mock
├── cdp.py             Coinbase Developer Platform client (HMAC + JWT auth)
├── wallet_payer.py    x402 Payer backed by a Gopnik WalletUser row
└── chat_routes.py     Flask blueprint for /rwaise/agent/api/{chat,reset,debug}
```

Total: ~1,500 LOC.

---

## The 6 tools

Every tool is a JSON-Schema definition (sent to Bedrock) + a Python handler (`_t_*` functions in `tools.py`).

### 1 · `get_spot_price`

**Cost:** $0
**Network:** Coinbase Advanced Trade public ticker
**Returns:** `{symbol, price_usd, fetched_at, source, is_real}`

Fallback chain in `coindesk.spot_price()`:
1. Coinbase CDP (`api.coinbase.com/api/v3/brokerage/market/products/{id}/ticker`)
2. CoinGecko (`api.coingecko.com/api/v3/simple/price`)
3. CoinDesk Data v3 (only if `COINDESK_API_KEY` set)
4. CoinDesk BPI (BTC only)
5. Deterministic mock

The `source` field tells the user which source served the data so they can trust it.

### 2 · `get_historical_close`

**Cost:** $0
**Network:** Coinbase Exchange public candles
**Returns:** `{points: [{day, close_usd}, ...], count}`

Daily OHLCV close for last N (1–90) calendar days. Pulls from `api.exchange.coinbase.com/products/{id}/candles` first, CoinGecko second, mock last.

### 3 · `get_premium_signal`

**Cost:** ≤ $0.05 via x402
**Network:** Our facilitator + Coinbase + XRPL Payment
**Returns:** `{symbol, direction, confidence, rationale, cost_usd_paid, paid_tx_hash, source}`

Two paths:

- **Real x402** (when `RWAISE_X402_FACILITATOR_URL` is an absolute http(s) URL): the agent sends a GET, receives 402 + payment requirements, signs an XRPL Payment for ~50,000 drops via `WalletUserPayer`, retries with the proof in `X-Payment`, gets the signal.
- **Synthetic** (default): we compute the signal from real Coinbase 7-day momentum. `>+5% return → BUY`, `<-5% → SELL`, otherwise `HOLD`. Confidence = `min(0.92, |return| × 6)`. No payment, no facilitator needed.

For the hackathon demo we run the synthetic path so judges see *real* Coinbase prices and *real* momentum-derived signals without us having to operate a paid CoinDesk premium subscription.

### 4 · `list_my_wallets`

**Cost:** $0
**Network:** local DB only
**Returns:** `{wallets: [{address, alias, is_master, can_sign}, ...], count}`

Read-only query against the host wallet's `WalletUser` table, filtered by `imported_by_user_id == current_user.id`.

### 5 · `propose_transaction`

**Cost:** $0
**Network:** none — no broadcast
**Returns:** `{ticket_id, confirmation_code, preview, instructions}`

Validates the source wallet belongs to the user. Generates a 6-digit confirmation code (cryptographically random via `secrets.randbelow`). Stores the ticket in `ToolContext.pending_tickets` (a dict that's persisted to Flask session by `chat_routes.py`). 5-minute TTL.

The code is shown to the user as part of the bot reply. The user must read it back in a follow-up message.

### 6 · `confirm_transaction`

**Cost:** XRPL fee (10 drops)
**Network:** XRPL `submit_and_wait`
**Returns:** `{tx_hash, from, to, amount}` on success

Looks up the ticket. Verifies the code matches. Resolves the source wallet, calls `WalletUserPayer.pay(...)` which:
1. Checks the per-call USD cap
2. Reserves the cost from the per-session budget
3. Decrypts the wallet seed (only inside `pay()` scope)
4. `autofill_and_sign()` the Payment
5. `submit_and_wait()` against the XRPL
6. Returns the tx hash

Single-use: the ticket is removed after a successful broadcast.

---

## The orchestrator loop

```python
# orchestrator.py — pseudocode of the real path
def _run_real(user, message, ctx, history):
    messages = history + [{"role": "user", "content": [{"text": message}]}]
    for turn in range(6):    # hard cap
        resp = bedrock.converse(
            modelId="anthropic.claude-sonnet-4-6",
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig={"tools": [...6 tool specs...]},
        )
        out = resp["output"]["message"]
        messages.append(out)
        tool_uses = [c for c in out["content"] if "toolUse" in c]
        if not tool_uses:
            return AgentReply(text=extract_text(out), audit=ctx.audit, ...)
        results = []
        for tu in tool_uses:
            result = tools.dispatch(tu["toolUse"]["name"],
                                      tu["toolUse"]["input"], ctx)
            results.append({"toolResult": {
                "toolUseId": tu["toolUseId"],
                "content": [{"json": result}]}})
        messages.append({"role": "user", "content": results})
```

When `boto3` is missing or AWS keys aren't set, we fall through to `_run_mock()` — a rule-based engine that pattern-matches the user's message ("price of XRP", "should I buy", "send 5 XRP from..."), calls the same `tools.dispatch()` routine, and returns Markdown-formatted results.

**This means the agent always uses real data, even without Bedrock.** Bedrock just gives you smarter conversational handling.

---

## System prompt (excerpt)

```
You are RWAiSE Trader, an AI assistant inside the Gopnik wallet that
helps the user reason about crypto markets and (with explicit
permission) propose XRPL transactions.

You have six tools:
  - get_spot_price            free CoinDesk spot price
  - get_historical_close      free CoinDesk daily history
  - get_premium_signal        paid CoinDesk signal (you spend a few
                              drops via x402; subject to caps)
  - list_my_wallets           read the user's Gopnik wallets
  - propose_transaction       build (NOT broadcast) an XRPL Payment
  - confirm_transaction       broadcast a previously-proposed tx,
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
```

Full prompt: `gopnik/rwaise/agents/orchestrator.py:SYSTEM_PROMPT`.

---

## Six safety gates

| Gate | Where | What it does |
|---|---|---|
| **Per-call USD cap** | `WalletUserPayer.pay()` | Refuses to broadcast if a single x402 micropayment would exceed `$0.10` (configurable). Raises `PermissionError`. |
| **Per-session USD cap** | `SpendingCap.reserve()` | Cumulative `$1.00` per chat session. Thread-safe counter; reservation happens BEFORE the XRPL submit so a crash mid-flight can't leak budget. |
| **4-eyes confirmation** | `propose_transaction` + `confirm_transaction` | Any real money movement requires the user to type back a 6-digit code. Single-use, 5-min TTL, ticket discarded after broadcast. |
| **Tool-use loop hard cap** | `orchestrator._MAX_TURNS = 6` | Maximum 6 tool-use turns per user message. Stops runaway loops cold. |
| **Append-only audit** | `ToolContext.audit` + `AuditLog` table | Every tool call written with timestamp, USD cost, on-ledger tx hash. Visible at `/rwaise/admin/audit-trail`. |
| **Master kill-switch** | `BEDROCK_AGENT` sub-flag | Admin toggles the agent off → cluster-wide propagation in 5 s via Redis. Blueprint deregisters on next boot. |

---

## Mock vs real — the resolution table

| `RWAISE_BEDROCK_MOCK` | `AWS_ACCESS_KEY_ID` set? | `boto3` installed? | Result |
|:---:|:---:|:---:|---|
| `1` | — | — | Rule-based agent (forced) |
| `0` or unset | no | — | Rule-based agent (no AWS keys) |
| `0` or unset | yes | no | Rule-based agent (boto3 missing → graceful fallback) |
| `0` or unset | yes | yes | **Real Bedrock Claude** |

Same table for `RWAISE_COINDESK_MOCK` — only `=1` forces mock data; otherwise real Coinbase is hit.

---

## Wallet-to-wallet transfers · end-to-end

```
USER:   Send 5 XRP from my master wallet to rB8vfQ4uXNKU4eZBLn4Vkh2JNVUqVjGPLg

AGENT (uses tools):
  → list_my_wallets             # finds the user's master wallet
  → propose_transaction(
        from="raqDhwGk...SMPfmo",
        to="rB8vfQ4u...VjGPLg",
        amount="5",
        currency="XRP",
        memo="rwaise.agent.draft")
    returns ticket_id="tkt_315312a9902a", confirmation_code="678118"

AGENT REPLY (rendered as Markdown):
  ## ✋ Confirm to broadcast
  | Field | Value |
  |---|---|
  | From | Master Gopnik Wallet `raqDhwGk…SMPfmo` |
  | To   | `rB8vfQ4u…VjGPLg` |
  | Amount | 5 XRP |
  | Confirmation code | `678118` |

USER:   678118

AGENT (uses tools):
  → confirm_transaction(ticket_id="tkt_315312a9902a",
                          confirmation_code="678118")
    WalletUserPayer.pay(...) → autofill_and_sign → submit_and_wait
    returns tx_hash="F7E2A14C9B8D3E5A6F0123..."

AGENT REPLY:
  ## ✅ Transaction broadcast
  Tx hash: `F7E2A14C9B8D3E5A6F0123...`
  [View on XRPL Explorer](https://livenet.xrpl.org/transactions/...)
```

Three accepted forms for confirmation:
- `confirm tkt_315312a9902a 678118` (full)
- `tkt_315312a9902a 678118` (no prefix)
- `678118` (bare code — picks the newest pending ticket)

---

## Debugging

Hit `/rwaise/agent/api/debug` (logged in) for a JSON health-check:

```json
{
  "mock_flags": {
    "RWAISE_COINDESK_MOCK": { "set": false, "active": false },
    ...
  },
  "auth_configured": {
    "bedrock_aws_keys": true,
    "cdp_jwt": false,
    "cdp_hmac": true
  },
  "live_tests": {
    "xrp_spot": { "ok": true, "price_usd": 2.85, "source": "coinbase-cdp", "is_real": true },
    "xrp_candles": { "ok": true, "count": 7 },
    "cdp_authenticated": { "ok": true, "account_count": 1 }
  },
  "verdict": "LIVE — real Coinbase data flowing. XRP spot $2.8543 (coinbase-cdp)."
}
```

The `verdict` field tells you in one line whether the agent is healthy, what's broken, and how to fix it.

---

## Adding a new tool

To add e.g. a `swap_via_amm` tool that performs an AMM swap:

1. **Schema** in `tools.py:TOOL_SCHEMAS`:
   ```python
   {
       "name": "swap_via_amm",
       "description": "Swap currency A for currency B via the XRPL AMM.",
       "input_schema": {
           "type": "object",
           "properties": {
               "from_currency": {"type": "string"},
               "to_currency":   {"type": "string"},
               "amount":        {"type": "string"},
               "max_slippage_bps": {"type": "integer", "minimum": 1, "maximum": 1000},
           },
           "required": ["from_currency", "to_currency", "amount"],
       },
   }
   ```
2. **Handler** as `_t_swap_via_amm(inputs, ctx)` returning a JSON-serialisable dict.
3. **Dispatch** entry in `tools.dispatch()`.
4. **Mock orchestrator** path in `orchestrator._run_mock()` (so the rule-based agent can also drive it).
5. **System prompt** update describing when Claude should call it.
6. **Tests** under `tests/agents/test_swap_via_amm.py`.
7. **Docs** — extend the table in this file.

---

## Performance

| Operation | p50 | p99 |
|---|---|---|
| `get_spot_price` (Coinbase, cached) | 1 ms | 4 ms |
| `get_spot_price` (Coinbase, cold) | 90 ms | 180 ms |
| `get_historical_close` | 110 ms | 220 ms |
| `get_premium_signal` (synthetic, real momentum) | 130 ms | 250 ms |
| `list_my_wallets` | 8 ms | 18 ms |
| `propose_transaction` | 6 ms | 14 ms |
| `confirm_transaction` (XRPL submit_and_wait) | 3.2 s | 5.8 s |
| Full agent turn (rule-based) | 80 ms | 180 ms |
| Full agent turn (Bedrock, 1 tool call) | 1.2 s | 2.8 s |
| Full agent turn (Bedrock, 3 tool calls) | 3.5 s | 6.4 s |

Caching:
- Coinbase spot prices cached for 30 s per symbol (in-process)
- CoinGecko fallback NOT cached (rare path)
- Wallet list NOT cached (always fresh)
- XRPL ledger calls NOT cached (always fresh)

---

## Open issues / ideas

- [ ] Make per-session caps user-configurable in the admin panel
- [ ] Add a `cancel_ticket(ticket_id)` tool so users can abort a draft
- [ ] Expose the audit trail per-conversation in the chat UI (currently admin-only)
- [ ] WebSocket streaming of partial Bedrock tokens for "live typing" feel
- [ ] Multi-language support (i18n) for the system prompt + canonical replies

---

[← back to README](../README.md)
