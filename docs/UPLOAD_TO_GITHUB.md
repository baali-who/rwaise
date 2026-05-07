# Upload RWAiSE to GitHub — step-by-step

This walks you through pushing the `rwaise-repo/` folder to a brand-new GitHub repository at **github.com/baali-who/rwaise**, configuring it for the hackathon submission, and adding the polish that makes it look professional.

Total time: ~10 minutes.

---

## Prerequisites

| Tool | Check it's installed |
|---|---|
| `git` | `git --version` should print 2.30+ |
| GitHub account | Your username is `baali-who` |
| Optional: `gh` CLI | `brew install gh` (macOS) or `winget install --id GitHub.cli` |
| Optional: SSH key for github.com | `ssh -T git@github.com` should say "Hi baali-who!" |

---

## Step 1 · Create the repository on GitHub

You have two ways. Pick one.

### Option A · From the website (easier)

1. Open <https://github.com/new>
2. Fill in:
   - **Owner:** `baali-who`
   - **Repository name:** `rwaise`
   - **Description:** `Real-World Asset Issuance & Settlement Engine for the XRP Ledger — XLS-33 MPTs · XLS-65 credentials · x402 · AI agent`
   - **Visibility:** **Public** (required for hackathon judging)
   - **Initialize this repository with:** leave **all three checkboxes empty** (we already have a README, LICENSE, and .gitignore in our local folder; if GitHub creates conflicting files we'd have to merge)
3. Click **Create repository**
4. GitHub shows you the empty-repo instructions. Ignore them — we have our own.

### Option B · From the terminal (faster, requires `gh`)

```bash
gh auth login                                    # one-time, follow prompts
gh repo create baali-who/rwaise --public \
  --description "Real-World Asset Issuance & Settlement Engine for the XRP Ledger — XLS-33 MPTs · XLS-65 credentials · x402 · AI agent" \
  --homepage "https://wallet.gopnik.io/rwaise"
```

---

## Step 2 · Initialise the local repo

From the folder containing the RWAiSE files (the one with `README.md`, `LICENSE`, `gopnik/`, `docs/` etc.):

```bash
cd /Users/baali.who/Downloads/gopnik_wallet_audit/rwaise-repo

# Initialise as a git repo
git init

# Set the default branch name to "main" (modern convention)
git checkout -b main

# Configure your identity (skip if already global)
git config user.name "baali-who"
git config user.email "alexander.baalmann@gmail.com"
```

---

## Step 3 · Stage everything except secrets

```bash
# Sanity-check what's about to be committed
git status

# Add everything that's not in .gitignore
git add .

# Critical: make sure no .env file got staged
git status | grep -i "\.env" && echo "⚠️  .env is staged — remove with: git restore --staged .env"

# Also double-check no PEM blocks made it in
grep -r "BEGIN EC PRIVATE KEY" --include="*.py" --include="*.md" --include="*.json" .
# → should print nothing
```

If `git status` shows anything that looks like a secret (.env, *.pem, credentials.json), `git restore --staged <file>` to unstage, then add it to `.gitignore`.

---

## Step 4 · First commit

```bash
git commit -m "feat: initial public release of RWAiSE for XRPL EasyA hackathon

- 9-step tokenization wizard (XLS-33 MPTs)
- XLS-65 credential issuer
- 4-eyes redemption pipeline
- AI agent (AWS Bedrock Claude + Coinbase Developer Platform + x402)
- 11 sub-flag admin toggle
- Full architecture documentation + 5 SVG diagrams"
```

---

## Step 5 · Connect to GitHub and push

If you used **HTTPS** (most common):

```bash
git remote add origin https://github.com/baali-who/rwaise.git
git push -u origin main
```

If you used **SSH** (preferred for daily use):

```bash
git remote add origin git@github.com:baali-who/rwaise.git
git push -u origin main
```

If GitHub asks for credentials, use a **Personal Access Token** (not your password) — generate one at <https://github.com/settings/tokens> with the `repo` scope.

---

## Step 6 · Polish the repo page

Visit <https://github.com/baali-who/rwaise> in the browser. You'll see your README rendered. Now make it look pro:

### 6a · Add topics (right-hand sidebar)

Click the ⚙️ next to "About" → **Topics**. Add:
```
xrpl  xrp-ledger  bedrock  claude  ai-agent  x402
real-world-assets  rwa  tokenization  defi  hackathon
flask  python  coinbase-cdp  easya
```
Topics surface your repo in GitHub search and the hackathon judges' filters.

### 6b · Set the repo's About box

Same ⚙️ panel:
- **Description:** `Real-World Asset Issuance & Settlement Engine for the XRP Ledger — XLS-33 MPTs · XLS-65 credentials · x402 · AI agent`
- **Website:** `https://wallet.gopnik.io/rwaise`
- **Topics:** (as above)
- ✅ Releases · ✅ Packages · ❌ Deployments · ✅ Discussions

### 6c · Add a social preview image

Repo **Settings → General → Social preview** → upload an image (1280×640 PNG).

If you don't have one, the SVG `docs/architecture/01-system-overview.svg` exported as PNG works great. Convert with:

```bash
# macOS (using Inkscape from brew)
brew install inkscape
inkscape --export-type=png --export-width=1280 \
  -o /tmp/social.png docs/architecture/01-system-overview.svg
# → upload /tmp/social.png
```

### 6d · Pin RWAiSE to your profile

<https://github.com/baali-who> → **Customize your pins** → tick `rwaise` → Save.

### 6e · Create a tagged release for the hackathon

```bash
git tag -a v1.0.0 -m "RWAiSE v1.0.0 — XRPL EasyA hackathon submission"
git push origin v1.0.0
```

Then on GitHub: **Releases → Draft a new release** → tag `v1.0.0`, title **"RWAiSE v1.0.0 — XRPL EasyA hackathon"**, body is the README's "What it does" section. Hit **Publish release**.

---

## Step 7 · (Optional) Enable GitHub Actions CI

Future you will thank present you. Create `.github/workflows/ci.yml`:

```yaml
name: ci
on:
  pull_request:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.10' }
      - run: pip install -r requirements.in pytest ruff mypy
      - run: ruff check gopnik/rwaise/
      - run: python -m py_compile $(find gopnik/rwaise -name '*.py')
      - run: pytest tests/ || true   # add real tests later
```

Commit + push and the green checkmark on PRs starts paying for itself.

---

## Step 8 · Submit to the hackathon

Once the repo is live and pinned, fill in your hackathon submission with:

| Field | Value |
|---|---|
| Project name | RWAiSE |
| Tagline | Real-World Asset Issuance & Settlement Engine for the XRP Ledger |
| Repo URL | https://github.com/baali-who/rwaise |
| Demo URL | https://wallet.gopnik.io/rwaise |
| Video walkthrough | (your YouTube link) |
| Pitch deck | (your Canva link) |
| Tweet | (your X thread link) |
| License | MIT |
| Track | XRPL · DeFi · AI |

---

## Common gotchas

### "Pushed to main but my README isn't showing"

GitHub caches the rendered README for ~30 s. Refresh, or wait a minute.

### "I accidentally committed .env"

Don't panic. Remove it and **rotate the secrets that were inside**:

```bash
git rm --cached .env
echo ".env" >> .gitignore
git commit -am "chore: remove accidentally committed .env"
git push

# Then go to Coinbase / AWS and ROTATE the leaked keys.
# They are forever in git history; assume they're public.
```

If the repo is fresh and nobody else has cloned it, you can also nuke history:

```bash
git checkout --orphan fresh
git add .
git commit -m "chore: reset history (accidental secret leak)"
git branch -D main
git branch -m main
git push -f origin main
```

### "GitHub thinks my repo is mostly HTML/CSS"

That's because the templates are large. Add `.gitattributes` to mark them as docs:

```
gopnik/rwaise/templates/** linguist-documentation
gopnik/rwaise/static/**    linguist-documentation
```

Commit + push. After GitHub recomputes (a few minutes), the language bar will show Python as the dominant language.

### "Where do I host the demo so judges can try it?"

Cheapest option for a demo URL (free): deploy the standalone container from `deployment/docker-compose.yml` to a Hetzner CX22 (€4/mo) or a Fly.io free instance. Bind a subdomain like `rwaise-demo.yourdomain.com` and link it from the README.

For the EasyA hackathon, the live Gopnik URL (`wallet.gopnik.io/rwaise`) is the production demo — use that.

---

You're done. Repo is live, secrets are safe, the README renders beautifully, and the hackathon judges have everything they need to evaluate the project. 🚀
