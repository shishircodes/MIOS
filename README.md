# MIOS Mode Monitor — Proof of Concept & Documents 

A working proof of concept for **Mode Monitor**, the first of three modes in the
**Market Intelligence Operating System (MIOS)** — a multi-agent AI pipeline
designed for industrial recruitment company **Easy Skill Australia**.

This PoC implements the four-stage Monitor pipeline end-to-end on a single
data source (PNGworkforce) and demonstrates the contract for the rest of MIOS.

---

## What this PoC does

```
┌─────────────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌─────────────────┐
│ 1. Scrape           │   │ 2. Store     │   │ 3. Classify      │   │ 4. Deliver      │
│ scraper/            │ ─ │ loader/      │ ─ │ agents/          │ ─ │ delivery/       │
│ pngworkforce · seek │   │ ingest.py    │   │ signal_analyst   │   │ slack.py        │
└─────────────────────┘   └──────────────┘   └──────────────────┘   └─────────────────┘
                                                       │
                                                       ▼
                                       ┌─────────────────────────────────┐
                                       │ KPI harness                     │
                                       │ evaluation/kpi_harness.py       │
                                       │ → results.csv + §5.2 KPI table  │
                                       └─────────────────────────────────┘
```

1. **Scrape** job postings from `pngworkforce.com` (PNG) and `au.seek.com` (AU)
   via the Apify SDK / `crawlee`. See [Data sources](#data-sources).
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
| `DB_PATH`               | Default `data/mios.db`                                                          |
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

## Data sources

| Source | Module | Geography | Records/run |
|---|---|---|---|
| PNGworkforce | `scraper/pngworkforce.py` | PNG | listing pages |
| SEEK | `scraper/seek.py` | AU | ~32 per category path |

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

## Run the tests

```bash
python -m pytest -q
```

Tests use mocked Gemini (no live API calls) and a saved HTML fixture for the
scraper, so they run offline and are reproducible.

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
│   ├── schema.sql            ← signals + watchlist DDL
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
