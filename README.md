# MIOS Mode Monitor — Proof of Concept

A working proof of concept for **Mode Monitor**, the first of three modes in the
**Market Intelligence Operating System (MIOS)** — a multi-agent AI pipeline
designed for industrial recruitment company **Easy Skill Australia**.

This PoC implements the four-stage Monitor pipeline end-to-end on a single
data source (PNGworkforce) and demonstrates the contract for the rest of MIOS.

> **AI assistance disclosure.** The PoC scaffolding was built with assistance
> from **Claude Code** (Anthropic). All architectural decisions, prompt design,
> dataset curation, KPI definitions, and final review were authored by the
> project team. See the wider project report for the Assessment 2 brief.

---

## What this PoC does

```
┌─────────────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌─────────────────┐
│ 1. Scrape           │   │ 2. Store     │   │ 3. Classify      │   │ 4. Deliver      │
│ scraper/            │ ─ │ loader/      │ ─ │ agents/          │ ─ │ delivery/       │
│ pngworkforce.py     │   │ ingest.py    │   │ signal_analyst   │   │ slack.py        │
└─────────────────────┘   └──────────────┘   └──────────────────┘   └─────────────────┘
                                                       │
                                                       ▼
                                       ┌─────────────────────────────────┐
                                       │ KPI harness                     │
                                       │ evaluation/kpi_harness.py       │
                                       │ → results.csv + §5.2 KPI table  │
                                       └─────────────────────────────────┘
```

1. **Scrape** job postings from `pngworkforce.com` (Apify SDK / `crawlee`).
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
python -m pip install -e .[dev]
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

> ⚠️ `.env` is gitignored. Never commit it.

---

## Run the pipeline

The KPI harness orchestrates the full pipeline against the labelled
synthetic dataset (`data/synthetic_postings.jsonl`).

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
│   └── pngworkforce.py       ← Apify SDK (crawlee) scraper, fails gracefully
├── loader/
│   ├── schema.sql            ← signals + watchlist DDL
│   └── ingest.py             ← UUID + dedupe-on-source_url ingestion
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
