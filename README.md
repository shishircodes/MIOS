# MIOS — Market Intelligence Operating System

A market intelligence platform for **Easy Skill Australia**, an industrial
recruitment company operating across Australia and Papua New Guinea.

MIOS answers two questions. **Monitor** asks *what is happening in the market?*
— it gathers hiring signals from job boards, classifies them with an LLM, and
produces a weekly digest. **Push** reverses it: given one consultant's profile,
*who in the market needs this specific person?* — it ranks companies by how well
their recent hiring activity fits that candidate, with the evidence behind every
score.

Python pipeline, FastAPI backend, React dashboard, Neon PostgreSQL, deployed by
GitHub Actions.

---

## Contents

1. [Quick start](#quick-start) — get it running locally
2. [Command reference](#command-reference) — every command and what it does
3. [The project](#the-project) — what it does and why it is built this way
4. [Data sources](#data-sources)
5. [Database](#database)
6. [Authentication](#authentication)
7. [Deployment](#deployment)
8. [Repository map](#repository-map)
9. [Current limitations](#current-limitations)

---

## Quick start

Python 3.11+ and Node 20+. Roughly five minutes from clone to a dashboard in
your browser.

### 1. Install

```bash
python -m pip install -e ".[api,dev]"
```

```bash
npm install --prefix web
```

`[api]` brings in FastAPI, Authlib and the CV readers; `[dev]` brings in pytest.

### 2. Configure

```bash
cp .env.example .env
```

The only value you need to start is `GEMINI_API_KEY`, from
[aistudio.google.com](https://aistudio.google.com) — free, no card. Everything
else has a working default: leave `DATABASE_URL` blank and it uses a local
SQLite file, leave the Slack and Adzuna keys blank and those features skip
themselves.

To browse the dashboard without setting up Google OAuth, add:

```
AUTH_DISABLED=true
```

This is a development bypass. The API logs a banner and the dashboard shows a
permanent warning strip, because it turns off every access control in the
system. Never set it on a deployed environment. For real sign-in, see
[Authentication](#authentication).

### 3. Create the database

```bash
python -m loader.check --init
```

Creates the tables and seeds the 20-company watchlist. Run this again whenever
the schema changes — it is idempotent and never drops anything.

### 4. Start the API server — first

```bash
python -m uvicorn api.server:app --reload --port 8787
```

The dashboard is a thin client: it renders nothing without the API. Start this
before the web server, or the first page load shows "Cannot reach the MIOS API".

Check it with <http://localhost:8787/api/health>.

> `uvicorn` may not be on your `PATH` even after installing. `python -m uvicorn`
> always works.

### 5. Start the web server

In a second terminal:

```bash
npm run dev --prefix web
```

Open <http://localhost:3000>.

### 6. Get some data

The dashboard falls back to a labelled sample dataset when the database is
empty, so it is populated from the first load. For real signals:

```bash
python -m pipeline.live --limit 20 --no-slack
```

Scrapes all four sources, classifies with Gemini, and refreshes the digest.
Takes a couple of minutes.

---

## Command reference

Every command in the project, grouped by what you are trying to do.

### Running the servers

| Command | What it does |
|---|---|
| `python -m uvicorn api.server:app --reload --port 8787` | Starts the API. `--reload` restarts on file changes. Serves the dashboard's data and Mode Push. |
| `npm run dev --prefix web` | Starts the dashboard on `:3000` with hot reload. Needs the API running. |
| `npm run build --prefix web` | Production build of the web app into `web/dist`. |
| `npm start --prefix web` | Serves that build. Used inside the Docker image, not for development. |
| `docker compose up --build` | Runs both as production containers. No hot reload — see [Docker](#running-the-stack-in-docker). |

### Running the pipeline

`pipeline.live` is the real cycle: scrape → classify → store → deliver.

| Command | What it does |
|---|---|
| `python -m pipeline.live` | Full cycle across all four sources, 50 records each, posts to Slack. |
| `python -m pipeline.live --limit 20` | Caps each source at 20 postings. Faster, uses less Gemini quota. |
| `python -m pipeline.live --source seek` | One source only. Repeatable: `--source seek --source newsfeed`. |
| `python -m pipeline.live --no-scrape` | Skips scraping; classifies whatever is already stored but unclassified. |
| `python -m pipeline.live --no-slack` | Runs everything but does not post the digest. |
| `python -m pipeline.live --days 14` | Widens the digest window from the default 7 days. |
| `python -m pipeline.live --db data/local.db` | Runs against a different database. |

Fresh scrapes **add** to the database; they never replace what is there. Signals
are deduplicated on `source_url`, so re-running does not create duplicates.

### Database

| Command | What it does |
|---|---|
| `python -m loader.check` | Reports the backend, server version and row counts. Run this first when something is wrong — it separates "can I reach the database?" from "does the pipeline work?". |
| `python -m loader.check --init` | The same, plus creates any missing tables and seeds the watchlist. Idempotent. **Run after every schema change.** |
| `python -m loader.migrate --dry-run` | Reports what a SQLite → Neon copy would move, without writing. |
| `python -m loader.migrate` | Copies `watchlist` and `signals` from SQLite into Neon. Idempotent — re-running after a partial failure tops up rather than duplicating. |
| `python -m loader.migrate --wipe` | Clears the target tables first. Destructive. |
| `python -m loader.rematch --dry-run` | Reports which signals would change watchlist tier under the current matching rules. |
| `python -m loader.rematch` | Recomputes `watchlist_tier` and `is_new_prospect` on stored signals. **No Gemini calls** — the watchlist match was always a string comparison, so changing the rules or the watchlist does not require reclassifying. |

> Mode Publish added the `reports` and `report_sections` tables, and roles added
> `app_users`. Run `loader.check --init` against Neon before deploying either —
> nothing applies a schema change to production on your behalf.

### Evaluation

| Command | What it does |
|---|---|
| `python -m evaluation.kpi_harness` | Classifies the labelled dataset five times, scores five metrics against ground truth, writes `results.csv` and prints a markdown KPI table. |
| `python -m evaluation.kpi_harness --runs 1` | Single run. Much faster; use while iterating on prompts. |
| `python -m evaluation.kpi_harness --no-slack` | Skips the digest post. |

This exists to measure classification quality on known-correct data. It never
touches live scraped signals.

### Tests

| Command | What it does |
|---|---|
| `python -m pytest` | The full suite. Gemini is mocked and scrapers use saved fixtures, so it runs offline and deterministically. |
| `python -m pytest tests/test_push_matcher.py` | One file. |
| `python -m pytest -k watchlist` | Everything matching a name. |
| `TEST_DATABASE_URL=postgresql://... python -m pytest` | Also runs the PostgreSQL parity tests, which skip otherwise. |

`TEST_DATABASE_URL` is deliberately separate from `DATABASE_URL`: the parity
tests truncate tables between cases, so a stray `pytest` must not be able to
reach your real data. Point it at a scratch database.

### Utilities

| Command | What it does |
|---|---|
| `python -c "import secrets; print(secrets.token_urlsafe(48))"` | Generates a `SESSION_SECRET`. |
| `npx --prefix web tsc --noEmit --project web` | Typechecks the web app without building. `--prefix` is needed because TypeScript lives in `web/node_modules`. |

---

## The project

### The two modes

**Mode Monitor** runs the weekly cycle. It scrapes three job boards, asks Gemini
to read each posting, and stores the result as a *signal*: not just the job ad,
but the interpretation of it — which company, what sector, which market, what
kind of evidence this is. Seven maintenance ads at one mine in a week is not
seven vacancies to fill; it suggests a shutdown is being planned and contractors
will be needed. The ad is the symptom, the signal is the reading.

Signals are classified into six categories — `hiring_velocity`, `project`,
`leadership`, `financial`, `competitive`, `market_intel` — and matched against a
20-company watchlist of Easy Skill's actual clients. The output is a dashboard
and a Slack digest.

**Mode Publish** turns the same intelligence outward. It assembles a quarterly
client-facing report — hiring trends across both markets — with every figure
counted from the signals collected in that quarter. Gemini then rewrites the wording — one call for the whole
report, on the same daily budget the classifier uses — but never the figures:
every number in its output is checked against the computed text, and a section
that introduces one is discarded. If the quota is gone the computed wording
ships and the report says so. A reviewer then works through it section by
section, and the report cannot be signed off until each one is approved. **MIOS has no endpoint that distributes anything**: §8.3 of the
project spec requires that gate to be architecturally enforced, so export hands
the document to a person and distribution happens outside the system.

**Mode Push** runs on demand. A consultant's contract ends in 30 days; the
business development team submits their profile and MIOS replies with a ranked
list of companies whose recent signals fit that person, each with the reasoning
behind the score and a recommended next action.

```
┌────────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────────┐
│ 1. Scrape  │  │ 2. Store │  │ 3. Classify  │  │ 4. Deliver   │
│ scraper/   │─►│ loader/  │─►│ agents/      │─►│ delivery/    │
│ png · seek │  │ ingest   │  │ signal_      │  │ slack + web  │
│ · adzuna   │  │          │  │ analyst      │  │ dashboard    │
└────────────┘  └──────────┘  └──────────────┘  └──────────────┘
                      │
                      │  signals
                      ▼
              ┌──────────────┐      ┌──────────────┐
              │ push/matcher │◄─────│ CV or form   │
              │ ranked list  │      │ push/parser  │
              └──────────────┘      └──────────────┘
```

### How Push scores a match

Deterministic, out of 100. No LLM.

| Weight | Contributor | Question |
|---|---|---|
| 35 | Role demand | Is this company hiring for the candidate's discipline? |
| 20 | Sector fit | Does the candidate's sector match theirs? |
| 15 | Hiring volume | How much are they hiring right now? |
| 12 | Relationship | Existing watchlist client, or a new name? |
| 10 | Region fit | Same market, or would they need to relocate? |
| 8 | Recency | How fresh are the signals? |

Every contributor emits an evidence line, so a score is always accompanied by
its reasons — "3 roles matching *Maintenance Planner*", "no PNG activity —
candidate would need to relocate". An LLM score would be neither reproducible
nor explainable, and *"94% match"* with no reason is not something a consultant
can act on. Weights live as named constants in `push/matcher.py` so they can be
tuned once the BD team can say what actually predicts a placement.

### CV parsing without an LLM

`.docx` and `.pdf` CVs are read and parsed by rules, not a model.

A general-purpose CV parser is genuinely hard. This one is not general-purpose:
Easy Skill recruits into mining, oil and gas, construction, defence and energy
transition across two countries, so a vocabulary of roles, sectors and places
covers the ground a model would otherwise be needed for — deterministically, for
free, and without sending anyone's CV to a third party.

`.docx` is read with the standard library. A `.docx` is a ZIP of XML and all
that is needed is the text inside `<w:t>` elements — a dozen lines of `zipfile`
against another dependency to pin. Legacy `.doc` is refused explicitly rather
than guessed at, because silently returning mojibake would be worse than a clear
error.

The parser returns a **draft**, not a record. Every field carries a confidence
level, the dashboard flags the uncertain ones, and a human confirms before
anything is saved. That is why the uploaded document never needs to be
retained — it is parsed in memory and discarded, so a CV never lands on disk or
in a backup. Only the fields matching actually consumes are stored.

### Why classification is split between Gemini and Python

Gemini reads the posting and answers questions that need judgement: what sector
is this, what kind of signal is it, who is the employer. Everything
deterministic stays in Python.

The watchlist match is the clearest example. Gemini guesses a company name;
Python maps that onto the canonical watchlist. That match runs in three stages —
exact match after stripping noise words like *Pty*, *Ltd* and *Australia*, then
whole-word containment so "BHP" matches "BHP Iron Ore" but never "BHPX
Services", then fuzzy matching at a strict threshold.

This ordering was learned the hard way. An earlier version used `rapidfuzz`'s
`WRatio`, whose partial-ratio component matches on fragments: 26 of 29 companies
tagged as clients were not clients, and three were competing recruitment
agencies labelled as Easy Skill's own accounts. Keeping the match in Python also
means it can be recomputed for free — `loader/rematch.py` fixed every stored row
without spending a single Gemini call.

A pre-filter also runs before the LLM: a minimum content length and a keyword
blocklist drop obviously irrelevant roles, so quota is not spent classifying
hospitality vacancies.

### Graceful degradation

A dead source degrades a run rather than killing it. Scrapers return `[]` on any
failure; the Slack poster returns `False` on a non-200 without raising. Missing
Adzuna keys skip that source and the other two still run. The pipeline always
completes and always says what it managed to do.

---

## Data sources

| Source | Module | Market | Method |
|---|---|---|---|
| PNGworkforce | `scraper/pngworkforce.py` | PNG | HTML listing pages |
| SEEK | `scraper/seek.py` | AU | HTML cards, ~32 per category path |
| Adzuna | `scraper/adzuna.py` | AU | **JSON API**, one search per watchlist company |
| Industry news | `scraper/newsfeed.py` | AU + PNG | **RSS**, one entry per article |

**The news source is the only one that isn't a job board**, and that matters
more than the effort saved building it. Job ads can only ever produce
`hiring_velocity`; a contract award, a financing round or a competitor winning
work is news. Those categories were nearly empty before this source existed.

RSS was chosen over everything else in the data-sources guide because it is a
published format meant to be polled — no key, no HTML selectors, no terms-of-use
tension — and the parser is standard library. Adding another publication is one
line in `FEEDS` or one entry in `NEWS_FEEDS`, not a new module.

> Several publishers listed in that guide sit behind Cloudflare and answer 403
> to any non-browser client regardless of User-Agent — Australian Mining, Energy
> Magazine, Infrastructure Magazine and Roads & Infrastructure among them.
> Getting past that is the browser-automation work this source exists to avoid,
> so they are not defaults. Check a feed returns 200 before adding it.

All four share one contract — `parse_listing(html, source_url, base_url)`, pure
and fixture-testable, and `scrape_async(limit, base_url)`, which never raises.
`scraper/__init__.py` registers them and fans out.

All sources are awaited in **one shared event loop**. Crawlee binds its storage
lock to the first loop it sees, so a per-source `asyncio.run()` breaks the second
source with an error about a different event loop.

**Adzuna is the simplest and the only one that is not scraping.** It calls a
documented API with a free key: no robots.txt carve-outs, no selectors to break
on a redesign, no terms-of-use tension. It also searches by keyword, so it asks
directly about watchlist companies rather than browsing a category and hoping
they appear. Get a key at
[developer.adzuna.com](https://developer.adzuna.com/); leave `ADZUNA_APP_ID` and
`ADZUNA_APP_KEY` blank to skip it.

Adzuna labels some salaries as predicted rather than advertised. Those are shown
as *(estimated)* rather than presented as a real figure.

### SEEK and robots.txt

`au.seek.com/robots.txt` disallows two things that shape the scraper entirely:

```
Disallow: */job/      # job DETAIL pages
Disallow: *?          # ANY url with a query string
```

So the scraper only fetches query-free category landing paths such as
`/jobs-in-mining-resources-energy`. Everything needed — title, company,
location, salary, teaser, posted date — is server-rendered on the listing card,
so detail pages are never opened. Card links to `/job/<id>` are stored as
`source_url` for stable deduplication, but never fetched.

Because SEEK paginates with `?page=2`, which is disallowed, each path yields only
its first page. **Breadth comes from adding category paths, not from paging** —
edit `DEFAULT_PATHS` in `scraper/seek.py` or set `SEEK_PATHS`.
`_assert_allowed()` rejects disallowed URLs at runtime, so a future path cannot
quietly break the rule.

> robots.txt is the machine-readable permission this honours. SEEK's website
> Terms of Use separately restrict automated collection, so this source is
> scoped to non-commercial academic use.

---

## Database

Runs on **Neon PostgreSQL** when `DATABASE_URL` is set, and a local **SQLite**
file otherwise.

Both are supported deliberately. Neon is the deployment target; SQLite keeps
`pytest` runnable offline and lets someone clone the repo and run the whole
pipeline without provisioning anything. `loader/db.py` is a thin adapter, not an
ORM — modules still write plain SQL, and it handles the three things that
actually differ between the engines: placeholder style, row access, and
duplicate-key errors.

| | `DATABASE_URL` set | Blank |
|---|---|---|
| Backend | Neon PostgreSQL | SQLite at `DB_PATH` |
| Used by | pipeline, API, digest, evaluation | same |
| Tests | never | always |

### Connecting to Neon

1. Sign up at [neon.tech](https://neon.tech) and create a project.
2. **Connection Details → Connection string** → copy the **URI** (not the `psql`
   command). Prefer the **pooled** endpoint — the hostname contains `-pooler` —
   since the API opens a connection per request.
3. Put it in `.env`:

```
DATABASE_URL=postgresql://USER:PASSWORD@ep-NAME-123.REGION.aws.neon.tech/DBNAME?sslmode=require
```

If the password contains `@`, `:`, `/` or `?`, percent-encode it (`@` → `%40`).
`sslmode=require` is added automatically if omitted — Neon refuses plaintext
connections, and the failure without it is an opaque dropped connection rather
than anything mentioning TLS.

Then `python -m loader.check --init`.

### Schema

One `loader/schema.sql` serves both engines, written in the subset they share.
Timestamps stay ISO-8601 `TEXT` rather than `timestamptz`: the strings sort
chronologically, which is all the code needs, and it keeps one schema instead of
two.

Seven tables: `signals`, `watchlist`, `kv_store`, `candidate_profiles` (Mode
Push), `reports` and `report_sections` (Mode Publish), and `app_users` (who
may sign in, and with what role).

> **Schema changes need `python -m loader.check --init` run against Neon.** The
> PR preview workflow applies it automatically to preview branches, but nothing
> applies it to production on your behalf.

`candidate_profiles` stores only what matching consumes, plus enough identity
for the BD team to know whose profile it is. These are real people; contact
details are optional and the CV itself is never stored.

---

## Authentication

The dashboard and every data endpoint require a signed-in Google account. Easy
Skill runs on Google Workspace, so staff use the account they already have and
access is restricted to the company domain.

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

OAuth 2.0 Authorization Code flow with PKCE via [Authlib](https://authlib.org/),
which handles OIDC discovery, PKCE, state/nonce and ID-token signature
verification. The session is a signed, HttpOnly cookie, so there is no session
table to maintain.

**Two layers, and only the backend one matters.** `AuthGate` in
`web/src/routes/__root.tsx` stops the dashboard rendering, but that is UX —
`require_user` returns 401 for every data endpoint regardless of what the browser
does. Client-side gating alone would be theatre: anyone could `curl` the API.

Domain restriction is enforced against the **`hd` (hosted domain) claim** in the
verified ID token, not the email suffix, which proves nothing on its own. The
`hd` parameter on the authorization request is only a hint that pre-fills the
account chooser; a user can edit it out of the URL.

### Google Cloud setup

1. [Console](https://console.cloud.google.com/) → create or select a project.
2. **APIs & Services → OAuth consent screen** → *Internal* if the project lives
   in the Easy Skill Workspace (this alone restricts sign-in to the
   organisation), otherwise *External*. Scopes: `openid`, `email`, `profile`.
3. **Credentials → Create credentials → OAuth client ID**, type **Web application**.
4. Under **Authorised redirect URIs** add, exactly:
   ```
   http://localhost:8787/auth/callback
   ```
5. Copy the client ID and secret into `.env`.

Paste the client ID whole — it already ends in `.apps.googleusercontent.com`.
Appending it again produces Google's opaque `invalid_client` error.

### Configuration

| Variable | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client from the console |
| `SESSION_SECRET` | Signs the session cookie. Blank ⇒ random per process, so sessions drop on restart |
| `SESSION_MAX_AGE` | Session lifetime in seconds (default `43200` = 12h) |
| `API_BASE_URL` / `WEB_APP_URL` | Where the API and dashboard live |
| `OAUTH_REDIRECT_URI` | Defaults to `<API_BASE_URL>/auth/callback`; must match the console exactly |
| `ALLOWED_GOOGLE_DOMAIN` | Workspace domain checked against the verified `hd` claim |
| `ALLOWED_EMAILS` | Comma-separated allowlist bypassing the domain check. Optional — the People & access screen does the same job without a restart — but still honoured, and listed there so it is not an invisible door |
| `AUTH_DISABLED` | Development bypass. Never in a deployed environment |

### Roles

Two roles. **Member** gets the intelligence pages; **administrator** also gets
the Admin section — source health, model spend, and the access list itself.

There are three ways in, and they differ in kind:

| Route | Grants | Managed from |
|---|---|---|
| `ALLOWED_GOOGLE_DOMAIN` | member | Server configuration. Admits everyone at the Workspace domain, and can never grant admin |
| `app_users` table | member **or** admin | The **Admin → People & access** screen. Works for any address, at any domain — this is how somebody outside Easy Skill gets in without widening the domain rule |
| `ALLOWED_EMAILS` | member | Server configuration plus a restart. Left in place deliberately: removing it would lock out whoever it names on the next deploy |

The People & access screen shows one list of everyone who can sign in, whichever
route let them in — an access route nobody can see is a route nobody revokes.
Rows from `ALLOWED_EMAILS` carry a lock instead of a Remove button, since closing
that door takes a configuration change and a restart. The domain rule admits
people who never appear as rows at all, so it is stated under the list.

`revgames7@gmail.com` is seeded as the first administrator on an empty database,
so a fresh deploy is never a locked room with the key inside. Seeding only fires
when there is no administrator at all, so it cannot undo a deliberate demotion.
The last remaining administrator cannot be demoted or removed.

Hiding the Admin nav group is presentation. Every `/api/admin/*` endpoint checks
the role server-side and answers a member with `403`.

`ALLOWED_EMAILS` may be left empty. Two things key off "can anyone outside the
domain sign in?" — the `hd` hint that filters Google's account chooser, and the
line on the sign-in screen — and both ask `access.has_external_grants()`, which
counts named rows as well as the environment variable. Reading the variable
directly would make an empty one mean "domain accounts only", hiding named
outsiders from the chooser and telling them on the sign-in page that they cannot
get in.

> **Sign-out caveat.** Because the session is a stateless signed cookie, sign-out
> is a client-side delete — there is no server-side record to revoke. A cookie
> copied off the machine stays valid until `SESSION_MAX_AGE` elapses, which is
> why that default is 12 hours rather than weeks. Server-side revocation would
> need a session table.

---

## Deployment

### CI/CD

Four workflows in `.github/workflows/`:

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | every PR + push to `main` | pytest against **both** SQLite and a real PostgreSQL service container, web typecheck + build, Docker images built **and started** |
| `pr-preview.yml` | PR opened/updated | Neon branch per PR, publishes images to GHCR, deploys a preview stack, comments the URLs |
| `pr-cleanup.yml` | PR closed/merged | deletes the Neon branch, tears down the containers |
| `deploy-main.yml` | `main` **after CI passes** | publishes images, applies the schema, deploys to the VPS |

`deploy-main.yml` chains off CI with `workflow_run`, so a red build never reaches
the server. `ci.yml` needs no secrets and runs on forks.

Running the tests against a real PostgreSQL container on every PR is what catches
dialect bugs that only appear on Neon — three were found this way, all invisible
against SQLite.

CI also **starts** the API image and waits for `/api/health`, not just builds it.
A build proves the Dockerfile parses; it does not prove the app runs. `Dockerfile.api`
copies packages in one by one, so **a new top-level package needs a `COPY` line
added** — miss it and the image builds cleanly, then fails when uvicorn imports
the app. That reaches the deploy as `container mios-api-1 is unhealthy`, which
names neither the module nor the file.

### What a PR gets

* **An isolated Neon branch** (`preview/pr-42`), copy-on-write from `main` —
  instant, and starts with real data. Writes never touch production.
* **Its own containers**, ports derived from the PR number (`30000+N` web,
  `40000+N` api) so concurrent previews never collide and the URL stays stable
  across pushes.
* **A comment** with both URLs, updated in place rather than spamming the thread.

Until the VPS secrets exist, the preview workflows still create the branch, build
the images, and comment what *would* have been deployed.

### GitHub configuration

**Settings → Secrets and variables → Actions.**

Variables:

| Variable | Example | Needed for |
|---|---|---|
| `NEON_PROJECT_ID` | `crimson-lab-12345678` | Neon branching |
| `NEON_PARENT_BRANCH` | `main` (default) | which branch to copy from |
| `NEON_DB_USER` / `NEON_DB_NAME` | `neondb_owner` / `neondb` | branch connection string |
| `PREVIEW_HOST` | `203.0.113.10` | preview deploys |
| `PROD_HOST` | VPS IP | production deploys |
| `PROD_WEB_URL` / `PROD_API_URL` | `https://mios.example.com` | production |
| `DEPLOY_USER` | `deploy` (default) | both |
| `ALLOWED_GOOGLE_DOMAIN` / `ALLOWED_EMAILS` | `easyskill.com` | production sign-in |

Secrets:

| Secret | Where from |
|---|---|
| `NEON_API_KEY` | Neon Console → Account settings → API keys |
| `DEPLOY_SSH_KEY` | **private** half of a keypair whose public half is in the VPS's `authorized_keys` |
| `PROD_DATABASE_URL` | Neon pooled connection string |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Cloud Console |
| `SESSION_SECRET` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL` | optional |

`GITHUB_TOKEN` is provided automatically — no setup for GHCR.

`PROD_WEB_URL` and `PROD_API_URL` are set explicitly rather than derived: they
depend on your domain and on where TLS terminates. `PROD_API_URL` is also baked
into the web bundle at build time and registered with Google, so a wrong value
fails quietly rather than loudly.

### Provisioning the server

Ubuntu 24.04:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
```

> `docker-compose-plugin` is Docker's own package name and only exists if you add
> their APT repo. Ubuntu ships the same v2 plugin as `docker-compose-v2`. Confirm
> with `docker compose version` — the space matters.

```bash
sudo useradd -m -s /bin/bash deploy && sudo usermod -aG docker deploy
```

```bash
sudo ufw allow 22 && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
```

Add the CI deploy key's public half to `/home/deploy/.ssh/authorized_keys`. For
previews, also open `30000-30999` and `40000-40999`.

### TLS is required, and it is on you

`docker-compose.prod.yml` runs the two app containers over plain HTTP. **It does
not terminate TLS.** Google rejects OAuth redirect URIs that are not `https`
(only `localhost` is exempt), so sign-in will not work until HTTPS is in front of
the API. Pick one:

| Option | How it works |
|---|---|
| **Cloudflare** (simplest) | Point your domain's nameservers at Cloudflare, add A records for both hostnames, set SSL mode to *Full*. TLS terminates at the edge; the VPS stays plain HTTP. Free. |
| **nginx + certbot on the host** | Terminate on the VPS and proxy to `127.0.0.1:3000` / `127.0.0.1:8787`. Set `WEB_BIND`/`API_BIND` to `127.0.0.1` so container ports are not public. |
| **A proxy container** | Add Traefik or nginx to the compose file. |

DNS is two A records at the VPS IP — `mios.example.com` for the dashboard and
`api.mios.example.com` for the API. Two names keeps the split simple without a
reverse proxy in the compose file; they share a registrable domain, so the
session cookie is still first-party.

Then register `https://api.mios.example.com/auth/callback` in the Cloud Console,
matching `PROD_API_URL` byte for byte — scheme, host, no trailing slash.

### Sign-in is disabled on previews

Google does not accept wildcard redirect URIs, so a per-PR hostname cannot be
registered in advance. Previews run with `AUTH_DISABLED=true`, which means
**anyone with the preview URL can read the dashboard**. The data is scraped
public job ads, but if that is not acceptable, restrict the port range to your
own IP at the firewall. Production runs on one fixed URL, so it registers a
single redirect URI and keeps auth on.

### Rolling back

Every deploy tags images with the commit SHA as well as `main`:

```bash
cd ~/mios && sed -i 's/:[0-9a-f]\{40\}/:<older-sha>/g' .env && docker compose -p mios -f docker-compose.prod.yml up -d
```

### Running the stack in Docker

```bash
docker compose up --build
```

Dashboard on <http://localhost:3000>, API on <http://localhost:8787>, configured
from your existing `.env`. Google Sign-In works here — `localhost` is the one
exception to Google's HTTPS rule.

**This is not a replacement for `npm run dev`.** There is no hot reload; editing
a file changes nothing until you rebuild. Use it to check the production
containers work before pushing.

| | Docker | Native |
|---|---|---|
| Hot reload | ✗ | ✓ |
| Matches production | ✓ | ✗ |
| Needs Python/Node installed | ✗ | ✓ |
| Start-up | ~2 min first build | seconds |

> `VITE_API_BASE` is baked into the web bundle at **build** time — Vite inlines
> `import.meta.env` into the client bundle, so it cannot be changed by setting an
> env var on a running container. That is why the preview workflow rebuilds the
> web image per PR.

---

## Repository map

```
.
├── config/
│   ├── settings.py           ← loads .env, exposes typed Settings
│   └── watchlist.json        ← 20-company watchlist (10 A / 7 B / 3 C)
├── scraper/
│   ├── __init__.py           ← source registry + shared-event-loop fan-out
│   ├── pngworkforce.py       ← crawlee scraper (PNG)
│   ├── seek.py               ← au.seek.com, robots.txt-constrained (AU)
│   └── adzuna.py             ← Adzuna JSON API, one query per client (AU)
├── loader/
│   ├── db.py                 ← Neon PostgreSQL / SQLite adapter
│   ├── schema.sql            ← all seven tables, one file, both engines
│   ├── ingest.py             ← UUID + dedupe-on-source_url ingestion
│   ├── check.py              ← connectivity probe / schema init
│   ├── migrate.py            ← SQLite → Neon copy
│   └── rematch.py            ← recompute watchlist matches, no LLM calls
├── agents/
│   ├── prompts.py            ← system prompt, classification template, blocklist
│   └── signal_analyst.py     ← Gemini classification + watchlist matching
├── push/                     ← Mode Push
│   ├── cv_extract.py         ← .docx via zipfile, .pdf via pypdf. No LLM
│   ├── profile_parser.py     ← rule-based CV → draft profile with confidences
│   ├── matcher.py            ← deterministic scoring + evidence
│   └── store.py              ← profile persistence + signal queries
├── api/
│   ├── server.py             ← FastAPI app; data endpoints require auth
│   ├── auth.py               ← Google Sign-In (OAuth2/OIDC) + require_user/require_admin
│   ├── access.py             ← roles, the three sign-in routes, last-admin guards
│   ├── admin_api.py          ← /api/admin/* — access list and source health
│   ├── digest_service.py     ← dashboard payload, windowing, velocity baseline
│   ├── publish_api.py        ← /api/publish/* — quarterly reports and review
│   └── push_api.py           ← /api/push/* — CV intake and matching
├── delivery/
│   ├── digest.py             ← Slack mrkdwn weekly digest + ranking helpers
│   └── slack.py              ← incoming-webhook poster
├── evaluation/
│   └── kpi_harness.py        ← 5-metric scoring + results.csv
├── pipeline/
│   └── live.py               ← the production cycle
├── web/                      ← TanStack Start + React 19 dashboard
│   └── src/routes/           ← digest, feed, push, publish, watchlist, sources,
│                                access, tokens
├── data/
│   └── synthetic_postings.jsonl  ← 80 hand-authored labelled postings
├── tests/                    ← pytest; Gemini mocked, scrapers fixture-driven
├── .github/workflows/        ← ci, pr-preview, pr-cleanup, deploy-main
└── docker-compose*.yml       ← local, preview, prod
```

---

## Current limitations

Stated plainly, because they shape what the system can honestly claim.

**Monitor does not run itself.** There is no scheduler. The weekly cycle runs
when someone types `python -m pipeline.live`. A cron entry on the VPS would
close this.

**The hiring-velocity baseline needs history.** Week-over-week change is
computed from earlier windows, and windows in which the pipeline did not run are
excluded rather than counted as zero — a week nobody scraped is not a week nobody
hired. Until several weekly runs have accumulated, the table honestly reports
"no baseline yet" rather than showing a comparison it cannot support.

**Only job-board signals.** All three sources are job boards, so
`hiring_velocity` dominates. Contract awards — *"Downer won a $340M rail
contract"* — are news, not job ads, and would need a news or ASX announcement
source. Internal conversation notes from the CRM are likewise not ingested, so
signals that come from consultant debriefs do not exist yet.

**Sign-out cannot be revoked server-side.** See the caveat under
[Authentication](#authentication).

**Gemini free tier is 20 calls per day.** Signals are batched 100 per call to
stay inside it, which is ample for weekly runs but not for continuous
classification.

---

## License

University coursework (ICT946) — internal use only.
