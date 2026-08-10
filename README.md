# MIOS Mode Monitor — Proof of Concept & Documents 

A working proof of concept for **Mode Monitor**, the first of three modes in the
**Market Intelligence Operating System (MIOS)** — a multi-agent AI pipeline
designed for industrial recruitment company **Easy Skill Australia**.

This PoC implements the four-stage Monitor pipeline end-to-end across three
data sources and demonstrates the contract for the rest of MIOS.

---

## What this PoC does

```
┌─────────────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌─────────────────┐
│ 1. Scrape           │   │ 2. Store     │   │ 3. Classify      │   │ 4. Deliver      │
│ scraper/            │ ─ │ loader/      │ ─ │ agents/          │ ─ │ delivery/       │
│ png · seek · adzuna │   │ ingest.py    │   │ signal_analyst   │   │ slack.py        │
└─────────────────────┘   └──────────────┘   └──────────────────┘   └─────────────────┘
                                                       │
                                                       ▼
                                       ┌─────────────────────────────────┐
                                       │ KPI harness                     │
                                       │ evaluation/kpi_harness.py       │
                                       │ → results.csv + §5.2 KPI table  │
                                       └─────────────────────────────────┘
```

1. **Scrape** job postings from `pngworkforce.com` (PNG), `au.seek.com` (AU)
   and the Adzuna API (AU). See [Data sources](#data-sources).
2. **Store** raw + processed signals in a local SQLite database.
3. **Classify** each signal with **Google Gemini 2.5 Flash** —
   `signal_category`, `review_cycle`, watchlist match (fuzzy via `rapidfuzz`),
   and `is_new_prospect` flag.
4. **Deliver** a formatted weekly digest to a Slack channel via incoming webhook.

A KPI harness re-runs the classification stage against a labelled ground-truth
set, scores five metrics, and writes `results.csv` for §5.2 of the report.

---

## How it relates to the wider MIOS architecture

MIOS as designed has three modes (Monitor / Push / Publish) and four LLM
agents. This PoC builds **Mode Monitor** + the **Signal Analyst** agent only —
just enough to demonstrate the four-stage pipeline end-to-end on real data.
Mode Push, Mode Publish, and the other three agents are scoped for a later
assessment. SQLite stands in for production BigQuery; the schema
(`loader/schema.sql`) mirrors the production design.

For the full architecture, see §4.1 of the project report.

---

## Setup

### 1. Clone

```bash
git clone <your-repo-url> mios-poc
cd mios-poc
```

### 2. Install dependencies (Python 3.11+)

```bash
python -m pip install -e ".[dev]"
```

### 3. Configure secrets

```bash
cp .env.example .env
```

Then edit `.env`:

| Variable                | How to get it                                                                  |
| ----------------------- | ------------------------------------------------------------------------------ |
| `GEMINI_API_KEY`        | https://aistudio.google.com → Get API key (free tier, no card required)         |
| `SLACK_WEBHOOK_URL`     | https://api.slack.com/apps → Create App → Incoming Webhooks → activate + copy  |
| `GEMINI_MODEL`          | Default `gemini-2.5-flash`                                                     |
| `DATABASE_URL`          | Neon PostgreSQL connection string. Blank = use SQLite. See [Database](#database--neon-postgresql) |
| `DB_PATH`               | SQLite fallback path. Default `data/mios.db`                                    |
| `LOG_LEVEL`             | Default `INFO`                                                                  |
| `PNGWORKFORCE_BASE_URL` | Default `https://www.pngworkforce.com`                                         |
| `SEEK_BASE_URL`         | Default `https://au.seek.com`                                                   |
| `SEEK_PATHS`            | Comma-separated category paths; blank = `scraper.seek.DEFAULT_PATHS`            |

Running the web dashboard also needs the API extra and Google OAuth credentials —
see [Authentication](#authentication--google-sign-in):

```bash
python -m pip install -e ".[api]"
```

> ⚠️ `.env` is gitignored. Never commit it.

---

## Database — Neon PostgreSQL

The pipeline runs on **Neon PostgreSQL** when `DATABASE_URL` is set, and falls
back to a local **SQLite** file otherwise.

Both are supported on purpose. Neon is the deployment target; SQLite keeps
`pytest` runnable offline and lets someone clone the repo and run the whole
pipeline without provisioning a database — reproducibility that a Postgres-only
cut would have thrown away. `loader/db.py` is a thin adapter, not an ORM:
modules still write plain SQL, and it handles the three things that actually
differ (placeholder style, row access, duplicate-key errors).

| | Set `DATABASE_URL` | Leave it blank |
|---|---|---|
| Backend | Neon PostgreSQL | SQLite at `DB_PATH` |
| Used by | pipeline, API, digest, KPI harness | same |
| Tests | never (see below) | always |

### 1. Create the Neon database

1. Sign up at [neon.tech](https://neon.tech) and create a project.
2. **Connection Details → Connection string** → copy the **URI** (not the `psql`
   command). Prefer the **pooled** endpoint — the hostname contains `-pooler` —
   since the API opens a connection per request.
3. Put it in `.env`:

```
DATABASE_URL=postgresql://USER:PASSWORD@ep-NAME-123.REGION.aws.neon.tech/DBNAME?sslmode=require
```

If the password contains `@`, `:`, `/` or `?`, percent-encode it (`@` → `%40`).
`sslmode=require` is added automatically if you omit it — Neon refuses plaintext
connections, and the failure without it is an opaque dropped connection rather
than anything mentioning TLS.

### 2. Check the connection

```bash
python -m loader.check
```

Reports the backend, server version and row counts. Add `--init` to create the
schema. Run this before anything else — it separates "can I reach Neon?" from
"does the pipeline work?", so a failure points at one thing rather than both.

### 3. Move your existing data across

```bash
python -m loader.migrate --dry-run
```

```bash
python -m loader.migrate
```

Copies `watchlist` and `signals` from the SQLite file into Neon. Idempotent —
rows insert with `ON CONFLICT DO NOTHING`, so re-running after a partial failure
tops up rather than duplicating. `--wipe` clears the target first.

### Schema

One `loader/schema.sql` serves both engines, written in the subset they share
(`CREATE TABLE IF NOT EXISTS`, partial unique indexes, `ON CONFLICT … DO UPDATE`).
Timestamps stay ISO-8601 `TEXT` rather than becoming `timestamptz`: the strings
sort correctly and it keeps one schema instead of two.

### Testing against PostgreSQL

Parity tests exist but are skipped unless a server is configured, via a
**separate** variable so a stray `pytest` cannot touch your Neon data:

```bash
TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_db_postgres.py
```

They truncate tables between tests — point them at a scratch database, never one
holding real signals.

---

## Data sources

| Source | Module | Geography | How |
|---|---|---|---|
| PNGworkforce | `scraper/pngworkforce.py` | PNG | HTML listing pages |
| SEEK | `scraper/seek.py` | AU | HTML cards, ~32 per category path |
| Adzuna | `scraper/adzuna.py` | AU | **JSON API**, one search per watchlist company |

**Adzuna is the simplest of the three and the only one that isn't scraping.** It
calls a documented API with a free key, so there are no robots.txt carve-outs to
honour, no selectors to break on a redesign, and no terms-of-use tension. It also
searches by keyword, so it asks directly about watchlist companies rather than
browsing a category and hoping they appear — much stronger input for the hiring
velocity table. Set `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` from
[developer.adzuna.com](https://developer.adzuna.com/); leave them blank and the
source is skipped while the other two still run.

Both expose the same contract — `parse_listing(html, source_url, base_url)` (pure,
fixture-testable) and `scrape_async(limit, base_url)` (never raises; returns `[]`
on any failure). `scraper/__init__.py` registers them and `scrape_all()` fans out,
so a dead source degrades a run rather than killing it. Every source is awaited in
one shared event loop: crawlee binds its storage lock to the first loop it sees, so
per-source `asyncio.run()` calls break the second source.

### SEEK and robots.txt

`au.seek.com/robots.txt` disallows two things that shape the scraper:

```
Disallow: */job/      # job DETAIL pages
Disallow: *?          # ANY url with a query string
```

So the scraper only fetches query-free category landing paths such as
`/jobs-in-mining-resources-energy`. Everything needed — title, company, location,
salary, teaser, posted date — is server-rendered on the listing card, so detail
pages are never opened. Card links to `/job/<id>` are stored as `source_url` (with
tracking params stripped, for stable dedupe) but never fetched.

Because SEEK paginates with `?page=2`, which is disallowed, each path yields only
its first page. **Breadth comes from adding category paths, not from paging** —
edit `DEFAULT_PATHS` in `scraper/seek.py` or set `SEEK_PATHS`. `_assert_allowed()`
rejects disallowed URLs at runtime so a future path can't quietly break the rule.

> Note: robots.txt is the machine-readable permission we honour here. SEEK's
> website Terms of Use separately restrict automated collection, so this source
> is scoped to non-commercial academic use for this PoC.

---

## Run the pipeline

There are **two** entry points, for different purposes:

### Live production-style cycle: `pipeline.live`

Scrapes every registered source live, classifies new postings via Gemini, builds
+ posts the weekly digest. **No scoring** — use this for real demos.

```bash
python -m pipeline.live                           # all sources (limit 50 each)
python -m pipeline.live --source seek             # one source only (repeatable)
python -m pipeline.live --limit 20                # cap each source to 20 postings
python -m pipeline.live --no-scrape               # classify pending only + post
python -m pipeline.live --no-slack                # build digest without Slack
python -m pipeline.live --days 14                 # widen digest window
```

### KPI evaluation harness: `evaluation.kpi_harness`

Loads the labelled synthetic dataset (`data/synthetic_postings.jsonl`),
classifies, scores against ground truth, writes `results.csv`, posts digest.
**Use this for §5.2 numbers in the report.**

```bash
python -m evaluation.kpi_harness                  # 5 evaluation runs + Slack digest
python -m evaluation.kpi_harness --runs 1         # single run
python -m evaluation.kpi_harness --no-slack       # skip Slack delivery
python -m evaluation.kpi_harness --runs 1 --db data/local.db
```

Outputs:

- `results.csv` — per-run scores + aggregate row
- stdout — markdown KPI table for §5.2 of the report
- Slack channel — formatted weekly digest

---

## Authentication — Google Sign-In

The web dashboard and every data endpoint require a signed-in Google account.
Easy Skill runs on Google Workspace, so staff use the account they already have
and access is restricted to the company domain.

### How it works

```
browser              FastAPI (:8787)              Google
  |  GET /auth/login   |                            |
  |------------------->| state + nonce + PKCE       |
  |<-- 302 to Google --|--------------------------->|
  |                    |                            | user consents
  |  GET /auth/callback?code=…&state=…              |
  |------------------->| verify state               |
  |                    | exchange code -> tokens    |
  |                    | verify ID token (JWKS)     |
  |                    | check hd / email_verified  |
  |<- 302 to web app --| session cookie set         |
```

OAuth 2.0 Authorization Code flow with PKCE, via [Authlib](https://authlib.org/) —
which handles OIDC discovery, PKCE, state/nonce and ID-token signature
verification. The session is a signed, HttpOnly cookie (Starlette
`SessionMiddleware`), so there is no session table to maintain.

**Two layers, and the backend one is what matters.** `AuthGate` in
`web/src/routes/__root.tsx` stops the dashboard rendering, but that is only UX —
`require_user` on the API returns 401 for every data endpoint regardless of what
the browser does. Client-side gating alone would be theatre: anyone could
`curl` the API directly.

Domain restriction is enforced against the **`hd` (hosted domain) claim** in the
verified ID token — not the email suffix, which proves nothing on its own. The
`hd` parameter sent on the authorization request is only a UX hint that pre-fills
the account chooser; a user can edit it out of the URL.

### Google Cloud setup

1. [Google Cloud Console](https://console.cloud.google.com/) → create/select a project.
2. **APIs & Services → OAuth consent screen** → choose *Internal* if the project
   lives in the Easy Skill Workspace (this alone restricts sign-in to the
   organisation), otherwise *External*. Scopes needed: `openid`, `email`, `profile`.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**,
   type **Web application**.
4. Under **Authorised redirect URIs** add — exactly, including the scheme:
   ```
   http://localhost:8787/auth/callback
   ```
5. Copy the client ID and secret into `.env`.

### Configuration

| Variable | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client from the console |
| `SESSION_SECRET` | Signs the session cookie. Blank ⇒ random per process (sessions drop on restart) |
| `SESSION_MAX_AGE` | Session lifetime in seconds (default `43200` = 12h) |
| `API_BASE_URL` / `WEB_APP_URL` | Where the API and dashboard live |
| `OAUTH_REDIRECT_URI` | Defaults to `<API_BASE_URL>/auth/callback`; must match the console exactly |
| `ALLOWED_GOOGLE_DOMAIN` | Workspace domain checked against the verified `hd` claim |
| `ALLOWED_EMAILS` | Comma-separated allowlist that bypasses the domain check (dev accounts) |
| `AUTH_DISABLED` | Dev-only bypass — see below |

Generate a session secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Running it

```bash
python -m uvicorn api.server:app --reload --port 8787
```

```bash
cd web && npm run dev
```

Then open <http://localhost:3000> — you'll get the sign-in screen until you
authenticate.

### Developing without Google credentials

Graders cloning this repo won't have an OAuth client. Setting `AUTH_DISABLED=true`
skips auth entirely so the dashboard is usable offline. It is deliberately loud
about it: the API logs a banner on startup and the dashboard shows a permanent
warning strip. **Never set this in a deployed environment.**

> **Sign-out caveat.** Because the session is a stateless signed cookie, sign-out
> is a client-side delete — there is no server-side record to revoke. A cookie
> copied off the machine stays valid until `SESSION_MAX_AGE` elapses, which is
> why that default is 12h rather than weeks. Server-side revocation would need a
> session table; out of scope for the PoC.

---

## CI/CD, previews and deployment

Four workflows in `.github/workflows/`:

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | every PR + push to `main` | pytest (against **both** SQLite and a real PostgreSQL service container), web typecheck + build, Docker image builds |
| `pr-preview.yml` | PR opened/updated | Neon branch per PR, publishes images to GHCR, deploys a preview stack, comments the URLs |
| `pr-cleanup.yml` | PR closed/merged | deletes the Neon branch, tears down the containers |
| `deploy-main.yml` | `main` **after CI passes** | publishes images, applies the schema, deploys to the Hetzner VPS behind HTTPS |

`deploy-main.yml` chains off CI with `workflow_run`, so a red build never reaches
the server.

`ci.yml` needs no secrets and runs on forks. The preview workflows degrade
gracefully: until the VPS secrets exist, they still create the branch, build the
images, and comment what *would* be deployed.

### What a PR gets

* **An isolated Neon branch** (`preview/pr-42`), copy-on-write from `main` — instant,
  and starts with real data. Writes never touch production.
* **Its own containers** on the VPS, ports derived from the PR number
  (`30000+N` web, `40000+N` api) so concurrent previews never collide and the
  URL stays stable across pushes.
* **A comment** with both URLs, updated in place rather than spamming the thread.

### Required GitHub configuration

**Settings → Secrets and variables → Actions.**

Variables (not secret):

| Variable | Example | Needed for |
|---|---|---|
| `NEON_PROJECT_ID` | `crimson-lab-12345678` | Neon branching |
| `NEON_PARENT_BRANCH` | `main` (default) | which branch to copy from |
| `NEON_DB_USER` | `neondb_owner` (default) | branch connection string |
| `NEON_DB_NAME` | `neondb` (default) | branch connection string |
| `PREVIEW_HOST` | `203.0.113.10` or `preview.example.com` | deploying |
| `DEPLOY_USER` | `deploy` (default) | deploying |

Secrets:

| Secret | Where to get it |
|---|---|
| `NEON_API_KEY` | Neon Console → Account settings → API keys |
| `DEPLOY_SSH_KEY` | private half of a keypair whose public half is in the VPS's `~/.ssh/authorized_keys` |
| `GEMINI_API_KEY` | only if you want previews to run classification |

`GITHUB_TOKEN` is provided automatically — no setup for GHCR.

### Production deployment (Hetzner VPS)

While the project is early, `main` deploys straight to production — there is no
separate staging tier.

```
                 Hetzner VPS
  internet ─► [ your TLS layer ] ─► web  (SSR, :3000)
                                 └► api  (FastAPI, :8787)
```

`docker-compose.prod.yml` runs the two app containers and publishes plain HTTP
ports. **It does not terminate TLS** — you put something in front.

#### TLS is required, and it is on you

Google **rejects OAuth redirect URIs that are not `https`** (only `localhost` is
exempt), so sign-in will not work until HTTPS is in front of the API. Pick one:

| Option | How it works |
|---|---|
| **Cloudflare** (simplest) | Point your Namecheap domain's nameservers at Cloudflare, add A records for both hostnames, set SSL mode to *Full*. TLS terminates at the edge; the VPS stays plain HTTP. Free. |
| **nginx + certbot on the host** | Terminate TLS on the VPS itself and proxy to `127.0.0.1:3000` / `127.0.0.1:8787`. Set `WEB_BIND`/`API_BIND` to `127.0.0.1` so the container ports are not exposed publicly. |
| **A proxy container** | Add Traefik or nginx to `docker-compose.prod.yml` yourself. |

Until then, deploys succeed and the app runs, but the dashboard cannot
authenticate.

#### DNS (Namecheap)

Two hostnames, both A records pointing at the VPS IP:

```
mios.example.com      ->  <vps-ip>     # dashboard
api.mios.example.com  ->  <vps-ip>     # API
```

Two names rather than one keeps the split simple without a reverse proxy in the
compose file. They share a registrable domain, so the session cookie is still
first-party; CORS is already configured from `WEB_APP_URL`.

#### Provision the VPS

On a fresh Hetzner box (Ubuntu 24.04):

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
```

> `docker-compose-plugin` is Docker's own package name and only exists if you
> add their APT repo. Ubuntu ships the same v2 plugin as `docker-compose-v2`.
> Confirm with `docker compose version` — the space matters.

```bash
sudo usermod -aG docker $USER   # log out and back in for this to apply
```

```bash
sudo useradd -m -s /bin/bash deploy && sudo usermod -aG docker deploy
```

```bash
sudo ufw allow 22 && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
```

Add your CI deploy key to `/home/deploy/.ssh/authorized_keys`. If TLS terminates
on the host, keep 3000/8787 closed and set `WEB_BIND=127.0.0.1` /
`API_BIND=127.0.0.1`. If you go the Cloudflare route, those ports must be
reachable — restrict them to Cloudflare's IP ranges rather than the whole world.

#### Register the redirect URI with Google

In the Cloud Console OAuth client, add **exactly**:

```
https://api.mios.example.com/auth/callback
```

It must match `PROD_API_URL` byte for byte — scheme, host, no trailing slash.

#### Required GitHub configuration for production

Variables:

| Variable | Example |
|---|---|
| `PROD_HOST` | `5.75.130.42` (VPS IP, used for SSH) |
| `PROD_WEB_URL` | `https://mios.example.com` |
| `PROD_API_URL` | `https://api.mios.example.com` |
| `DEPLOY_USER` | `deploy` (default) |
| `ALLOWED_GOOGLE_DOMAIN` | `easyskill.com` |
| `ALLOWED_EMAILS` | your account, while testing |

Secrets:

| Secret | Notes |
|---|---|
| `DEPLOY_SSH_KEY` | private half of the deploy keypair |
| `PROD_DATABASE_URL` | Neon pooled connection string |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | from the Cloud Console |
| `SESSION_SECRET` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL` | optional |

`PROD_WEB_URL` and `PROD_API_URL` are set explicitly rather than derived: they
depend on your domain and on where TLS terminates. `PROD_API_URL` is also baked
into the web bundle at build time and registered with Google, so a wrong value
fails silently rather than loudly.

#### Rolling back

Every deploy tags images with the commit SHA as well as `main`. To go back, SSH
in and point `.env` at an older SHA:

```bash
cd ~/mios && sed -i 's/:[0-9a-f]\{40\}/:<older-sha>/g' .env && docker compose -p mios -f docker-compose.prod.yml up -d
```

### Setting up a preview server

Once you have a Linux box:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
```

```bash
sudo useradd -m -s /bin/bash deploy && sudo usermod -aG docker deploy
```

Then generate a deploy key locally, add the **public** half to the server and the
**private** half to `DEPLOY_SSH_KEY`:

```bash
ssh-keygen -t ed25519 -f mios-deploy -C "github-actions" -N ""
```

Open the preview port range on the firewall (`30000-30999`, `40000-40999`), set
`PREVIEW_HOST`, and the next PR deploys automatically.

### Sign-in is disabled on previews

Google **does not accept wildcard redirect URIs**, so a per-PR hostname can't be
registered in advance. Previews therefore run with `AUTH_DISABLED=true` — the API
logs a banner and the dashboard shows a permanent warning strip.

That means **anyone with the preview URL can read the dashboard**. The data is
scraped public job ads, but if that isn't acceptable, restrict the port range to
your own IP at the firewall. Production runs on one fixed URL, so it registers a
single redirect URI and keeps auth on.

### Upgrading previews to real hostnames

The port-number scheme is deliberate: it needs no DNS. Once you own a domain, you
can move previews to `pr-42.preview.example.com` by adding a wildcard `*.preview`
A record, putting a reverse proxy in front, dropping the `ports:` blocks from
`docker-compose.preview.yml` and adding routing labels. Nothing else changes.

### Running the whole stack locally with Docker

```bash
docker compose up --build
```

Dashboard on <http://localhost:3000>, API on <http://localhost:8787>. Both images
build from source, and configuration comes from your existing `.env` — same Neon
database as the native setup.

Google Sign-In works here: `localhost` is the one exception to Google's
"no plain HTTP redirect URIs" rule, so the `http://localhost:8787/auth/callback`
you already registered is valid.

**This is not a replacement for `npm run dev`.** There is no hot reload — editing
a file changes nothing until you rebuild. Use it to check the production
containers work before pushing; use the native servers to actually write code.

| | Docker | Native (`npm run dev` + `uvicorn --reload`) |
|---|---|---|
| Hot reload | ✗ | ✓ |
| Matches production | ✓ | ✗ |
| Needs Python/Node installed | ✗ | ✓ |
| Start-up | ~2 min first build | seconds |

Rebuild after changes:

```bash
docker compose up --build --force-recreate
```

> `VITE_API_BASE` is baked into the web bundle at **build** time, so pointing the
> dashboard at a different API needs `docker compose build web`, not a restart.

### Building the images individually

```bash
docker build -f Dockerfile.api -t mios-api .
```

```bash
docker build --build-arg VITE_API_BASE=http://localhost:8787 -t mios-web web/
```

> `VITE_API_BASE` is baked in at **build** time — Vite inlines `import.meta.env`
> into the client bundle, so it cannot be changed by setting an env var on the
> running container. That's why the preview workflow rebuilds the web image per PR.

---

## Run the tests

```bash
python -m pytest -q
```

Tests use mocked Gemini (no live API calls) and a saved HTML fixture for the
scraper, so they run offline and are reproducible.

The PostgreSQL parity tests skip locally unless you point them at a scratch
database. CI always runs them against a Postgres service container, so dialect
bugs that only appear on Neon are caught on every PR:

```bash
TEST_DATABASE_URL=postgresql://... python -m pytest -q
```

---

## Repository map

```
.
├── config/
│   ├── settings.py           ← loads .env, exposes typed Settings
│   └── watchlist.json        ← 20-company watchlist (10 A / 7 B / 3 C)
├── scraper/
│   ├── __init__.py           ← source registry + scrape_all() fan-out
│   ├── pngworkforce.py       ← Apify SDK (crawlee) scraper, fails gracefully
│   └── seek.py               ← au.seek.com scraper, robots.txt-constrained
├── loader/
│   ├── db.py                 ← Neon PostgreSQL / SQLite adapter
│   ├── schema.sql            ← signals + watchlist DDL (both engines)
│   ├── check.py              ← `python -m loader.check` connectivity probe
│   ├── migrate.py            ← `python -m loader.migrate` SQLite → Neon copy
│   └── ingest.py             ← UUID + dedupe-on-source_url ingestion
├── api/
│   ├── auth.py               ← Google Sign-In (OAuth2/OIDC) + require_user gate
│   ├── digest_service.py     ← digest payload for the web app
│   └── server.py             ← FastAPI bridge; data endpoints require auth
├── agents/
│   ├── prompts.py            ← SYSTEM_PROMPT + classification template + blocklist
│   └── signal_analyst.py     ← Gemini 2.5-flash classification + fuzzy watchlist
├── delivery/
│   ├── digest.py             ← 5-section Slack mrkdwn weekly digest
│   └── slack.py              ← incoming-webhook poster
├── evaluation/
│   └── kpi_harness.py        ← 5-metric scoring + results.csv + §5.2 table
├── data/
│   ├── synthetic_postings.jsonl  ← 80 hand-authored labelled postings
│   └── mios.db                   ← SQLite (gitignored, generated)
├── tests/                    ← pytest, mocks Gemini + fixture-driven scraper
├── demo/                     ← placeholder for screen recording (step M5.3)
├── pyproject.toml
├── .env.example
└── README.md                 ← you are here
```

---

## Module choices (decisions worth defending in Q&A)

- **Scraper:** `crawlee[beautifulsoup]` (the Apify SDK for Python) over the
  hosted-actor + `APIFY_TOKEN` path. The local SDK is reproducible by graders
  with no Apify account dependency. Brief allowed either.
- **LLM:** `gemini-2.5-flash` rather than the brief's `gemini-2.0-flash`. 2.0
  Flash was deprecated in 2025; 2.5 Flash is the current free-tier equivalent.
  Override via `GEMINI_MODEL` in `.env`.
- **Watchlist match in Python, not in the prompt.** Gemini guesses the company
  name; `rapidfuzz` (threshold 85, WRatio scorer) maps it onto the canonical
  watchlist with alias support. Keeps the LLM call deterministic and cheap.
- **Pre-filter before the LLM.** `MIN_CONTENT_LENGTH=50` + a 30-keyword
  blocklist (marketing/hospitality/retail/etc.) drops obvious non-Easy-Skill
  roles. Logged so we can quote the real filtered percentage in §5.2.
- **Graceful degradation.** Scraper returns `[]` on any failure; Slack poster
  returns `False` on non-200 without raising. The pipeline always completes.

---

## Known PoC scope limits

- Only one source (PNGworkforce). Mode Monitor in production would have ~12.
- Only the Signal Analyst agent. The Conversation Analyst, Aggregator, and
  Strategist agents are out of scope for Assessment 2.
- The 80-record synthetic set doubles as the ground-truth set; the brief's
  aspirational 200-record set is not yet authored.
- Slack digest "quality" KPI is filled in manually after human review.

---

## License

University coursework — internal use only.
