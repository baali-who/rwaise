# Contributing to RWAiSE

Thanks for considering a contribution. RWAiSE is an open-source plugin used in production by the Gopnik wallet, so we keep a high bar on code quality and security. Read this end-to-end before opening your first PR.

## Code of conduct

We follow the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Be excellent to each other.

## Local dev setup

```bash
git clone https://github.com/baali-who/rwaise.git
cd rwaise

# Create a venv
python3.10 -m venv .venv
source .venv/bin/activate

# Install in editable mode (assuming you have a host Gopnik checkout next door)
pip install -r requirements.in
pip install -e .

# Apply schema
psql -d gopnik_dev < deployment/sql/rwaise_migration.sql

# Copy + edit env
cp deployment/env.example .env
# Edit .env — at minimum set RWAISE_ENABLED=1 and your COINBASE_API_KEY/SECRET

# Run the host Gopnik wallet (NOT included in this repo) with rwaise mounted.
# See docs/INSTALLATION.md for the host-side wiring.
flask --app wsgi run --debug
```

Hit `http://localhost:5000/rwaise/agent` to verify.

## Code style

- **Black** for formatting (line length 88)
- **Ruff** for linting (`ruff check gopnik/rwaise/`)
- **mypy** for type hints on new code (strict on `gopnik/rwaise/agents/`)
- Docstrings: Google style, every public function gets one
- Imports: stdlib → third-party → local, alphabetised within groups
- No emojis in code; emojis are fine in docstrings if they aid readability

```bash
# Run all checks before pushing:
black gopnik/rwaise/ tests/
ruff check gopnik/rwaise/ tests/
mypy gopnik/rwaise/agents/
pytest tests/
```

## Commit messages

Conventional Commits flavour:

```
<type>(<scope>): <subject>

<optional body — what + why, never how>

<optional footer — breaking-change notice, related issue>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `security`.
Scopes (pick one): `agents`, `wizard`, `marketplace`, `x402`, `cdp`, `admin`, `templates`, `ci`, `deploy`.

Examples:

```
feat(agents): add CoinGecko fallback for symbols Coinbase doesn't list
fix(x402): close socket on facilitator timeout (resolves #42)
docs(deployment): add Secrets Manager rotation runbook
security(cdp): never log decrypted EC private key on JWT failure
```

## PR checklist

Before requesting review:

- [ ] Tests pass: `pytest tests/`
- [ ] Type check passes for changed agent code: `mypy gopnik/rwaise/agents/`
- [ ] Lint passes: `ruff check gopnik/rwaise/`
- [ ] You added or updated docs for any user-visible change
- [ ] You updated [`CHANGELOG.md`](CHANGELOG.md) if the change is notable
- [ ] No secrets / API keys / PEM blocks anywhere in the diff
- [ ] PR description explains *what* + *why*, screenshots for UI changes

## Branching

- `main` — protected, always deployable
- `iter-NN-<scope>` — per-iteration feature branches
- `fix/<short>` — small bug fixes off `main`

## Security disclosures

Found a vulnerability? **Do not open a public issue.** Email **security@rwaise.dev** with:
- A description of the issue
- Reproduction steps
- Impact assessment

We respond within 48 hours and follow [coordinated disclosure](https://www.cisa.gov/coordinated-vulnerability-disclosure-process).

## Areas where help is most welcome

- 🧩 More tools for the AI agent (NFT mint/transfer, AMM swaps, escrow create)
- 🌐 i18n — currently English-only
- 🧪 Property-based tests for the wizard state machine
- ⚡ Performance — get the marketplace under 100 ms TTFB at 100 concurrent users
- 📚 Tutorials — "Tokenize your first asset" video walkthrough

Thanks for helping push real-world assets onto the XRP Ledger 🚀
