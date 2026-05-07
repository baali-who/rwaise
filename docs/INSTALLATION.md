# Installation Guide

How to install RWAiSE into a host Gopnik wallet (or any Flask app), wire it up, and get the agent answering real questions.

There are three install paths. Pick one:

| Path | Use when | Time |
|---|---|---|
| **A · Drop-in plugin** | You have a Gopnik wallet repo already | 10 min |
| **B · Standalone dev container** | You want to demo locally without touching Gopnik | 15 min |
| **C · Production AWS Fargate** | You're deploying for real | See [`DEPLOYMENT.md`](DEPLOYMENT.md) |

---

## Path A — Drop-in plugin (recommended)

### Prerequisites

- Existing Gopnik wallet checkout on disk
- Python 3.10+ in the host's venv
- PostgreSQL 15 already running for Gopnik
- Redis 6+ already running for Gopnik (optional but recommended)

### Step 1 · Copy the plugin source into the host

```bash
cd /path/to/gopnik_wallet/

# Clone RWAiSE somewhere temporary
git clone https://github.com/baali-who/rwaise.git /tmp/rwaise

# Copy just the plugin folder into Gopnik's package
cp -r /tmp/rwaise/gopnik/rwaise gopnik/rwaise

# Copy the SQL migration
mkdir -p deployment/sql
cp /tmp/rwaise/deployment/sql/rwaise_migration.sql deployment/sql/

# Copy the example env (don't overwrite an existing .env)
cp /tmp/rwaise/deployment/env.example .env.rwaise.example
```

### Step 2 · Add the dep

```bash
echo "PyJWT>=2.8" >> requirements.in    # optional, only for CDP JWT auth
pip-compile requirements.in              # regenerate requirements.txt
pip install -r requirements.txt
```

### Step 3 · Apply the schema delta

```bash
psql -d gopnik < deployment/sql/rwaise_migration.sql
```

The migration is **idempotent** — safe to re-run. It creates ~12 `rwaise_*` tables and a few enum types.

### Step 4 · Wire `register_plugin()` into your Flask factory

Find your `create_app()` function (usually `gopnik/__init__.py`). Add **one line** near the bottom, after all core blueprints are registered:

```python
def create_app(config_name="default"):
    app = Flask(__name__)
    # ... existing setup ...

    # ─── RWAiSE plugin (iter-21+) ──────────────────────────
    from .rwaise import register_plugin as register_rwaise
    register_rwaise(app)

    return app
```

That single call:
- Imports the plugin's models (so SQLAlchemy registers them)
- Pushes feature-flag state into `app.config`
- Registers 7 blueprints (gated by sub-flags)
- Adds the RWAiSE tab to Gopnik's top nav via a Jinja context processor
- Registers admin sidebar links
- Wires `flask rwaise *` CLI commands

### Step 5 · Set env vars and restart

Append to your existing `.env`:

```bash
RWAISE_ENABLED=1
RWAISE_FEATURE_BEDROCK_AGENT=1

# Coinbase keys (real market data, no AWS needed)
COINBASE_API_KEY=<your api key id>
COINBASE_API_SECRET=<your secret>
```

Restart your Gopnik process:

```bash
gunicorn -c gunicorn.conf.py wsgi:app
```

### Step 6 · Flip the master switch

1. Sign in as admin
2. Open `https://your-host/rwaise/admin/feature-flags`
3. Toggle the **master switch** ON
4. Toggle the **BEDROCK_AGENT** sub-flag ON
5. Visit `/rwaise/agent` and type "What is the price of XRP?"

You should see a real price from Coinbase, source pill **`coinbase-cdp`**.

---

## Path B — Standalone dev container

If you don't have a Gopnik checkout and just want to demo:

```bash
git clone https://github.com/baali-who/rwaise.git
cd rwaise
cp deployment/env.example .env

# Edit .env — set at minimum:
#   COINBASE_API_KEY, COINBASE_API_SECRET
#   SECRET_KEY (any 32+ char random string)

docker compose -f deployment/docker-compose.yml up
```

The compose file (provided in `deployment/docker-compose.yml`) brings up:
- Postgres 15 with the rwaise schema pre-loaded
- Redis 6
- A minimal Flask host that mounts ONLY the rwaise plugin (no other Gopnik features)

Visit `http://localhost:5000/rwaise/agent`. Sign in with the seeded admin user (`admin@example.com` / `Admin1234!`). Type a question.

---

## Path C — Production AWS Fargate

See the full runbook: [`DEPLOYMENT.md`](DEPLOYMENT.md).

The short version:
1. ECR push the docker image
2. Register a Fargate task definition with `secrets:` mappings to AWS Secrets Manager
3. Update an ECS service to roll the new image
4. Apply migrations against RDS
5. Hit `/rwaise/agent/api/debug` to confirm `verdict: LIVE`

---

## Verifying the install

After setup, hit these three URLs:

| URL | Expected | Meaning |
|---|---|---|
| `/rwaise/` | Heroic landing page renders with gradient cards | Plugin mounted, CSS loads |
| `/rwaise/admin/feature-flags` | Master switch toggle visible (admin only) | Admin hooks wired |
| `/rwaise/agent/api/debug` | JSON with `verdict: "LIVE — real Coinbase data flowing."` | Real data flowing end-to-end |

If any of those fail, see the [Troubleshooting](#troubleshooting) section below.

---

## Troubleshooting

### "404 on /rwaise/" after restart

The blueprint isn't registered. Check:
- `RWAISE_ENABLED=1` in env
- `register_rwaise(app)` is called in your Flask factory
- No exception during plugin load — grep your logs for `RWAiSE plugin: failed to`

### "500 on /rwaise/wizard/step/1"

Most common cause: the `rwaise_*` tables aren't migrated. Re-run:

```bash
psql -d gopnik < deployment/sql/rwaise_migration.sql
```

If that doesn't fix it, paste the traceback from the logs.

### Agent returns `_mock_` source instead of `coinbase-cdp`

You have one of these env vars still set to `1`:
- `RWAISE_COINDESK_MOCK`
- `RWAISE_AGENT_MOCK`
- `RWAISE_BEDROCK_MOCK`

Unset all three. Restart. Hit `/rwaise/agent/api/debug` to confirm.

### Agent crashes with `ModuleNotFoundError: No module named 'boto3'`

You set `AWS_ACCESS_KEY_ID` but `boto3` isn't installed. Two fixes:
- **Easy:** unset `AWS_ACCESS_KEY_ID` — agent falls back to rule-based mode (still real data)
- **Better:** add `boto3>=1.34` to `requirements.in`, recompile, redeploy

### "RWAiSE Trader" tab missing from top nav

Open the admin panel and verify the master switch is ON. If it is, check the browser console for CSP errors — RWAiSE templates load CSS via the `head` Jinja block, which Gopnik's `base.html` must declare. If your fork of Gopnik renamed it, edit `gopnik/rwaise/templates/rwaise/_base.html` to use the correct block name.

---

## Next steps

- Read [`AGENT.md`](AGENT.md) to understand how the AI agent works internally
- Read [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full system map
- Read [`DEPLOYMENT.md`](DEPLOYMENT.md) when you're ready for production

Welcome to RWAiSE 🚀
