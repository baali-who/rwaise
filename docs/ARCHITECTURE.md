# Architecture · text walkthrough

This is the long-form companion to the 5 SVG diagrams in [`architecture/`](architecture/). Read them together: the SVGs give you the visual map, this doc gives you the why.

---

## 1 · System overview

> **Diagram:** [`01-system-overview.svg`](architecture/01-system-overview.svg)

Four lanes from left to right.

**Lane 1 — Client (purple).** A user opens the Gopnik wallet in their browser. They land on `/rwaise/agent` for the AI chat, `/rwaise/wizard` to tokenize an asset, or `/rwaise/marketplace` to browse listings. The browser-side JavaScript (`agent_chat.js`) is served as a real static file — not inline — because Flask-Talisman's CSP blocks inline `<script>` and `onsubmit=` handlers. The JS includes a tiny Markdown renderer (no deps) so the agent's replies render properly with tables, code, lists, headers, and autolinks.

**Lane 2 — RWAiSE plugin (aqua).** This is everything in `gopnik/rwaise/`. The host Gopnik wallet calls `register_plugin(app)` once at startup, which:
- Imports the SQLAlchemy models so they register on the shared `db`
- Pushes 11 feature-flag values into `app.config`
- Conditionally registers 7 Flask blueprints (each gated by its own sub-flag)
- Adds the **RWAiSE** tab to Gopnik's top nav via a Jinja `context_processor`
- Appends "Plugin feature flags", "Redemption queue", "Audit trail" links to the admin sidebar
- Registers the `flask rwaise *` CLI command group

The plugin's tokenization wizard, marketplace, AI agent, x402 protocol, admin surfaces, and developer-platform API all live here.

**Lane 3 — External services (pink).** Two AWS services (Bedrock Runtime + supporting infra) and the Coinbase Developer Platform. The agent calls Bedrock for Claude responses and Coinbase Advanced Trade for real spot prices and historical candles. An optional x402 facilitator wraps CoinDesk premium endpoints with HTTP 402 — when configured, the agent pays per call in XRP drops.

**Lane 4 — XRP Ledger (gold).** The settlement chain. RWAiSE uses XLS-33 (Multi-Purpose Tokens) for issuance, XLS-65 (layered credentials) for compliance, XLS-66 (permissioned DEX) for the order book, XLS-71 (ODL routing) for cross-currency settlement in the redemption pipeline, and ad-hoc XRPL Payment transactions for x402 micropayments and user-initiated transfers.

---

## 2 · AWS module map

> **Diagram:** [`02-aws-modules.svg`](architecture/02-aws-modules.svg)

Five service groups, each with multiple AWS components.

### 2.1 · Compute

- **ECS Fargate · `gopnik-web`** — 2 always-on tasks, each `1 vCPU + 2 GB`, deployed to `awsvpc` private subnets across two AZs. Rolling deploys with `minimumHealthyPercent=100` give zero downtime.
- **ECR** — container registry. Tag scheme `gopnik:rwaise-vN.N.N`. Image scan on push, lifecycle policy keeps the last 10.
- **ALB · `gopnik-prod-alb`** — single HTTPS listener with an ACM-issued cert, AWS WAF rate-limiting, sticky sessions for the wizard. Target group health-checks `/health` every 30 s.

### 2.2 · State

- **RDS Postgres 15 · `gopnik-prod`** — `db.t4g.medium`, multi-AZ, 50 GB encrypted with the customer-managed KMS key. 7-day automated backups + point-in-time recovery. Hosts the 12 `rwaise_*` tables alongside the rest of the Gopnik schema.
- **ElastiCache Redis 7 · `gopnik-prod`** — `cache.t4g.micro` with 1 primary + 1 replica. Stores the 5-second feature-flag cache and the `flask-limiter` token bucket.
- **S3 · `rwaise-uploads`** *(optional)* — for cap-table CSVs, prospectus PDFs, and IPFS-style MPT metadata JSON. Server-side encryption (SSE-S3) and versioning ON.

### 2.3 · AI · Bedrock

- **Bedrock Runtime · `anthropic.claude-sonnet-4-6`** — the agent calls `client.converse()` with a tool_use schema for the 6 RWAiSE tools. Streaming enabled. Region: `eu-north-1`.
- **Bedrock Agent (planned for iter-22)** — alternative deployment path using AWS's managed agent runtime + Lambda action groups. Same 6 tools, less hand-rolled orchestration.
- **Bedrock Guardrails (planned for iter-23)** — output filter to block PII leakage and jailbreak attempts.

### 2.4 · Security · Identity

- **Secrets Manager** — `gopnik/coinbase_api_key`, `gopnik/coinbase_api_secret`, `gopnik/db_password`, `gopnik/flask_secret_key`. Mapped into the Fargate task via the `secrets:` block; never appears in plain task-def env vars.
- **KMS** — one customer-managed key encrypts Secrets Manager, RDS at rest, S3 (SSE-KMS), and optionally wraps wallet seeds in the future HSM-signer path.
- **IAM** — `ecsTaskExecutionRole` (pull ECR + ship logs) and `gopnik-task-role` (call Bedrock + read Secrets). Reviewed monthly by IAM Access Analyzer.

### 2.5 · Network · Edge

- **VPC** — 2 public + 2 private subnets across 2 AZs. NAT gateway for egress. VPC flow logs to CloudWatch. Four security groups: `alb`, `fargate`, `rds`, `redis`.
- **Route 53** — A record for `wallet.gopnik.io` → ALB. CAA records pinned. Failover to a maintenance page on health-check failure.
- **CloudFront + WAF (planned for iter-23)** — CDN in front of static assets and a Lambda@Edge x402 facilitator at the edge instead of in Flask.

### 2.6 · Observability · Operations

- **CloudWatch Logs** — `/ecs/gopnik-web` log group. Structured JSON output. 30-day retention with subscription filter to S3 cold storage. Insights queries pre-saved for agent activity.
- **CloudWatch Metrics** — built-in (ALB 5xx, ECS CPU/memory, RDS connections) + custom (`rwaise.agent.cost_usd`, `rwaise.x402.payments`).
- **SNS** — alerts route to PagerDuty + Slack. P1 (5xx spike), P2 (Bedrock errors), P3 (agent cost runaway), P4 (feature-flag DB drift).
- **X-Ray (planned for iter-24)** — distributed tracing across Bedrock → Coinbase → XRPL spans.
- **Backups · DR** — daily RDS snapshots cross-region replicated. RTO 1 h, RPO 5 min. Tested quarterly.

Cost estimate: **~$290/month** for the entire stack with 10K Bedrock invocations (see `DEPLOYMENT.md §12`).

---

## 3 · AI agent flow

> **Diagram:** [`03-ai-agent-flow.svg`](architecture/03-ai-agent-flow.svg)

A 10-step end-to-end trace of "Should I buy XRP?" with the brain (Claude Sonnet 4.6) at the center and the 6 tools laid out below.

The orchestrator runs a tool-use loop with a hard cap of 6 turns per user message. When AWS keys + boto3 are available, real Claude drives the loop. Otherwise a rule-based engine pattern-matches the user's message and dispatches the same 6 tools — so the agent **always uses real Coinbase data**, even without Bedrock.

Full deep-dive: [`AGENT.md`](AGENT.md).

---

## 4 · x402 sequence

> **Diagram:** [`04-x402-sequence.svg`](architecture/04-x402-sequence.svg)

Sequence diagram with 4 lifelines (Agent · WalletUserPayer · Facilitator · XRPL) and 8 numbered messages showing the full `402 → pay → 200` cycle.

Why on XRPL specifically:
- **Sub-second finality** (~3-5 s vs 12 s on Base, ~30 s on Ethereum L1)
- **Fees of $0.000001/tx** vs ~$0.10/tx on EVM L1 — makes per-call micropayments economically viable
- **Native XRP and RLUSD** — no bridges, no wrapped tokens
- **The XRPL has had built-in payment + escrow + DEX since 2012** — we don't need a smart contract layer for x402, just plain Payment transactions with memos

---

## 5 · Class diagram

> **Diagram:** [`05-class-diagram.svg`](architecture/05-class-diagram.svg)

Module-by-module breakdown with classes, public methods, and key relationships. Useful for navigating the codebase the first time.

Six packages, with the AI agent (`agents/`) being the largest:

| Package | LOC | Purpose |
|---|---|---|
| **core** (`plugin.py`, `feature_flag.py`, `models.py`, `forms.py`) | ~600 | Plugin entry, 3-layer flag cascade, SQLAlchemy models, WTForms |
| **agents/** | ~1,500 | AI agent: orchestrator, 6 tools, CDP, CoinDesk adapter, WalletPayer, chat routes |
| **x402/** | ~400 | Server middleware + facilitator + client SDK helper |
| **wizard** (`wizard_controller.py`, `templates/wizard/`) | ~800 | 9-step state machine, Jinja templates, embedded co-pilot |
| **services/** | ~1,000 | Domain services (cap_table_loader, credential_issuer, orderbook, ...) |
| **admin/**, **api/** | ~700 | Admin sub-blueprints, Developer Portal, OAuth + paid public API |

**Total** ~5,000 LOC of Python.

---

## 6 · Data flow walkthroughs

### 6a · "User asks about XRP price"

```
Browser → POST /rwaise/agent/api/chat
      → orchestrator.run(user, "what's the price of XRP?")
        → Bedrock.converse(messages, tools=[...])
          ← tool_use: get_spot_price(symbol="XRP")
        → tools.dispatch("get_spot_price", {"symbol":"XRP"}, ctx)
          → coindesk.spot_price("XRP")
            → cdp.cdp_spot_price("XRP")
              → GET https://api.coinbase.com/api/v3/brokerage/market/products/XRP-USD/ticker
              ← {"price": "2.85", ...}
          ← SpotPrice(symbol="XRP", price_usd=2.85, source="coinbase-cdp")
        ← {"symbol":"XRP","price_usd":2.85,"source":"coinbase-cdp"}
        → Bedrock.converse(messages + tool_result, tools=[...])
          ← text: "XRP is $2.85 right now (source: Coinbase)..."
      ← AgentReply(text="...", cost_usd=0)
    ← JSON {text, audit, cost_usd, tools_used}
Browser ← renders Markdown with table
```

### 6b · "User asks for a paid signal"

```
Browser → POST /chat (message="should I buy XRP?")
      → orchestrator.run(user, message)
        → Bedrock first calls get_spot_price (free)
        → Bedrock then calls get_premium_signal
          → tools._t_premium_signal(...)
            → coindesk.premium_signal("XRP", payer=WalletUserPayer(...), max_pay_usd=0.05)
              → _x402_signal(...)   [if facilitator URL is absolute]
                → GET /premium/signals/XRP → 402 + payment_requirements
                → payer.pay(destination, 50000 drops, "XRP", nonce)
                  → cap.reserve(0.025)   [enforce per-session budget]
                  → autofill_and_sign(Payment) → submit_and_wait → tx_hash
                ← tx_hash
                → GET /premium/signals/XRP + X-Payment header → 200 + signal
              ← PremiumSignal(direction="buy", confidence=0.78, ...)
        ← {"direction":"buy","confidence":0.78,"cost_usd_paid":0.025,"paid_tx_hash":"..."}
      → Bedrock generates final reply citing both data points
    ← AgentReply
```

### 6c · "User initiates a wallet-to-wallet transfer"

```
TURN 1
  Browser → "send 5 XRP from master to rB8..."
  orchestrator → list_my_wallets (find source) + propose_transaction
  propose_transaction:
    - validate from_address belongs to user
    - generate ticket_id + 6-digit code (cryptographic random)
    - store in ToolContext.pending_tickets
    - DOES NOT BROADCAST
  AgentReply(text="✋ Confirm with code 678118")
  chat_routes saves tickets to Flask session

TURN 2
  Browser → "678118"
  orchestrator detects bare 6-digit code
  → confirm_transaction(ticket_id=newest, confirmation_code="678118")
    - validate code
    - WalletUserPayer.pay() → autofill → sign → submit_and_wait
    - returns tx_hash
  AgentReply(text="✅ Broadcast — tx_hash F7E2...")
  Ticket discarded
```

---

## 7 · Why these design choices

### Why a plugin, not a fork?

The Gopnik wallet has 1,000+ files of established code. Forking would mean perpetual rebases and weakening Gopnik's security posture by mixing concerns. As a drop-in plugin:
- Gopnik upgrades don't break RWAiSE
- Master kill-switch turns the entire feature off in 5 s if needed
- All RWAiSE tables share the `rwaise_*` prefix → easy to back up / migrate / drop
- The plugin can be open-sourced (MIT) without exposing Gopnik's proprietary code

### Why three layers of feature flags?

| Layer | Speed | Granularity | Use case |
|---|---|---|---|
| Env var floor | restart-time | per-process | Force-on a flag that admins should not be able to disable |
| DB toggle | instant write, 5 s prop | cluster-wide | Day-to-day admin UI |
| Redis cache | 5 s TTL | in-process | Avoid DB hit on every request |

This means: an admin can flip a toggle in the UI and within 5 seconds every Fargate task in the cluster sees the new value, without restarts, without coordinating with deploy, without waking ops at 3 AM.

### Why the propose-then-confirm ticket flow?

Three reasons:
1. **No prompt injection can move money** — even if Claude somehow hallucinates a transaction it shouldn't, it can only return a ticket. The user has to type a code that Claude has never seen before to actually broadcast.
2. **Recoverable mistakes** — if the user mis-types the destination, they can simply not confirm. The 5-min TTL ensures stale drafts don't accumulate.
3. **Audit trail clarity** — every broadcast has a paired propose+confirm pair in the audit log. Forensics is straightforward.

### Why Coinbase first, CoinGecko second?

Coinbase is the fastest source we found (p50 90 ms) and supports our 13 most-traded symbols. CoinGecko has wider coverage (RLUSD, USDT, GOPNIK, ...) but is rate-limited harder. The fallback is automatic — there is no "switch to CoinGecko" button.

### Why x402 settled on XRPL?

x402 was originally designed for USDC on Base (an EVM L2). RWAiSE's contribution is **the first XRPL implementation** of the protocol, settling in native XRP drops with sub-second finality. This isn't just chain-shopping — XRPL's payment-first design means the protocol fits more naturally there than on a smart-contract chain.

### Why a rule-based fallback for the agent?

Bedrock is great but adds operational complexity (model access requests, region availability, per-tenant quotas, cold-start latency). The rule-based fallback means:
- The agent works for offline demos / hackathons / dev
- The agent works during AWS outages
- The agent works for users in regions without Bedrock
- New developers can extend it without an AWS account

The fallback uses the same 6 tools and the same real Coinbase data — it's "less smart conversational handling", not "less data".

---

## 8 · Future work (in iter-22 → iter-26)

| Iter | What | Why |
|---|---|---|
| 22 | Bedrock AgentCore deployment path | AWS-canonical agent infra · session memory · IAM-backed |
| 23 | CloudFront + Lambda@Edge x402 facilitator | Move x402 verification to the edge, off-load Flask |
| 24 | MCP server wrappers for the 6 tools | Lets LangChain / CrewAI / AutoGen drive RWAiSE |
| 25 | Real XLS-66 ledger cutover | Move order-book matching from app-layer to ledger-native |
| 26 | Live XLS-71 ODL routing for redemption | Fiat off-ramps via the XRPL bridge currency router |

---

[← back to README](../README.md)
