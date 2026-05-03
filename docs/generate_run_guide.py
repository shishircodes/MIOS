"""Generate docs/MIOS-PoC-RunGuide.pdf — the operator's guide.

Run:  python docs/generate_run_guide.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor, black, grey
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

OUT_PATH = Path(__file__).resolve().parent / "MIOS-PoC-RunGuide.pdf"

ACCENT = HexColor("#1f6feb")
CODE_BG = HexColor("#f3f4f6")
CODE_BORDER = HexColor("#d1d5db")

styles = getSampleStyleSheet()

H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=20, leading=24,
                    textColor=ACCENT, spaceBefore=4, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=14, leading=18,
                    textColor=ACCENT, spaceBefore=14, spaceAfter=6)
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, leading=14,
                    textColor=black, spaceBefore=10, spaceAfter=4,
                    fontName="Helvetica-Bold")
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10, leading=14,
                      alignment=TA_LEFT, spaceAfter=6)
NOTE = ParagraphStyle("Note", parent=BODY, fontSize=9, leading=12,
                      textColor=grey, leftIndent=8, rightIndent=8,
                      borderPadding=4)
CODE = ParagraphStyle("Code", parent=styles["Code"], fontSize=9, leading=12,
                      backColor=CODE_BG, borderColor=CODE_BORDER, borderWidth=0.5,
                      borderPadding=6, leftIndent=4, rightIndent=4, spaceAfter=8,
                      fontName="Courier")

story: list = []


def h1(text):
    story.append(Paragraph(text, H1))


def h2(text):
    story.append(Paragraph(text, H2))


def h3(text):
    story.append(Paragraph(text, H3))


def p(text):
    story.append(Paragraph(text, BODY))


def note(text):
    story.append(Paragraph("<b>Note.</b> " + text, NOTE))


def code(text):
    safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br/>").replace("  ", "&nbsp;&nbsp;"))
    story.append(Paragraph(safe, CODE))


def kv_table(rows, col_widths=(4.5 * cm, 11.5 * cm)):
    t = Table([[Paragraph(f"<b>{k}</b>", BODY), Paragraph(v, BODY)] for k, v in rows],
              colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, CODE_BORDER),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))


# =====================================================================
# Title page
# =====================================================================

h1("MIOS Mode Monitor &mdash; PoC Run Guide")
p("<b>Project:</b> Market Intelligence Operating System (MIOS) &mdash; Mode Monitor "
  "Proof of Concept, Easy Skill Australia.")
p("<b>Audience:</b> Member 5 (build owner). Step-by-step instructions for running "
  "the PoC end-to-end on your local machine, plus a reference of what every code "
  "file does.")
p("<b>Repository root:</b> <font face='Courier'>E:\\UniProjects\\MIOS</font>")
p("<b>Python:</b> 3.11 or later. Tested on 3.14 / Windows 11.")

note("AI assistance disclosure. The PoC scaffolding was built with assistance from "
     "Claude Code (Anthropic). All architectural decisions, prompt design, dataset "
     "curation, and final review were authored by the project team.")

# =====================================================================
# Section 1: How to run
# =====================================================================

h1("1. How to run the PoC")

h2("1.1 First-time setup")
p("Open a terminal at the repository root.")
code("cd /e/UniProjects/MIOS\n"
     "python -m pip install -e .[dev]")
p("This installs the runtime dependencies "
  "(<font face='Courier'>google-genai</font>, "
  "<font face='Courier'>crawlee[beautifulsoup]</font>, "
  "<font face='Courier'>rapidfuzz</font>, "
  "<font face='Courier'>requests</font>, "
  "<font face='Courier'>python-dotenv</font>) plus pytest for the dev extras.")

h3("Configure secrets")
p("Copy the env template, then edit <font face='Courier'>.env</font> with your real "
  "values. <font face='Courier'>.env</font> is gitignored and must never be committed.")
code("cp .env.example .env")

kv_table([
    ("GEMINI_API_KEY",
     "Get from https://aistudio.google.com (free tier, no card needed)."),
    ("GEMINI_MODEL",
     "Default <font face='Courier'>gemini-2.5-flash</font>. Switch to "
     "<font face='Courier'>gemini-2.5-flash-lite</font> if the daily quota of the "
     "main model is exhausted &mdash; it has a separate quota pool."),
    ("SLACK_WEBHOOK_URL",
     "Create at https://api.slack.com/apps &rarr; Create App &rarr; Incoming Webhooks "
     "&rarr; Activate &rarr; Add New Webhook to Workspace &rarr; copy the URL."),
    ("DB_PATH",
     "Default <font face='Courier'>data/mios.db</font>. The file is created on first "
     "run and gitignored."),
    ("LOG_LEVEL",
     "Default <font face='Courier'>INFO</font>. Set <font face='Courier'>DEBUG</font> "
     "if you want fuzzy-match score traces."),
    ("PNGWORKFORCE_BASE_URL",
     "Default <font face='Courier'>https://www.pngworkforce.com</font>."),
])

h2("1.2 Run the unit tests (offline, no API or Slack calls)")
code("python -m pytest -q")
p("Expect <b>40 passed</b>. Tests mock the Gemini client and use a saved HTML "
  "fixture for the scraper, so they run with no network.")

h2("1.3 End-to-end live run")
p("The KPI harness orchestrates the full pipeline: load synthetic ground truth into "
  "SQLite &rarr; classify with Gemini &rarr; score against ground truth &rarr; "
  "write <font face='Courier'>results.csv</font> &rarr; build the weekly digest "
  "&rarr; post to Slack.")

h3("Single run (recommended first)")
code("python -m evaluation.kpi_harness --runs 1")
p("Runtime: about <b>9&ndash;10 minutes</b>. The 7&nbsp;s inter-call delay between "
  "Gemini requests is required to stay below the 10&nbsp;RPM free-tier ceiling. "
  "Outputs:")
p("&bull; <font face='Courier'>results.csv</font> &mdash; per-run scores and "
  "an aggregate row.")
p("&bull; A markdown KPI table on stdout, matching the &sect;5.2 report format.")
p("&bull; A weekly digest delivered to your Slack channel.")

h3("Five runs (for the &sect;5.2 numbers in the report)")
code("python -m evaluation.kpi_harness --runs 5")
p("Runtime: about 50 minutes. The harness wipes the signals table between runs but "
  "keeps the watchlist and writes mean + stdev for the latency KPI.")

h3("Useful flags")
code("python -m evaluation.kpi_harness --runs 1 --no-slack    # skip Slack delivery\n"
     "python -m evaluation.kpi_harness --db data/local.db     # custom db path\n"
     "python -m evaluation.kpi_harness --ground-truth data/my.jsonl")

h3("If quota is exhausted")
p("The free tier is approximately 200&nbsp;requests-per-day per model. One full run "
  "(80 records minus blocklist filters) uses ~60 calls. If you hit "
  "<font face='Courier'>RESOURCE_EXHAUSTED 429</font> errors, switch model or wait "
  "until the next day:")
code("# Linux / macOS / git-bash\n"
     "GEMINI_MODEL=gemini-2.5-flash-lite python -m evaluation.kpi_harness --runs 1\n\n"
     "# PowerShell\n"
     "$env:GEMINI_MODEL=&quot;gemini-2.5-flash-lite&quot;; "
     "python -m evaluation.kpi_harness --runs 1")

h2("1.4 Standalone live scrape (best-effort)")
p("To attempt a live PNGworkforce fetch:")
code("python -c &quot;from scraper.pngworkforce import scrape; "
     "r = scrape(limit=10); print(len(r), 'jobs found')&quot;")
p("Returns an empty list and logs a warning if the site's HTML does not match the "
  "heuristic selectors &mdash; that's the brief's required behaviour. The pipeline "
  "still runs against the synthetic dataset regardless.")

h2("1.5 After the run &mdash; deliverables for the report")
p("&bull; Open <font face='Courier'>results.csv</font> and copy the aggregate row "
  "into &sect;5.2 of the project report. Replace the three "
  "<font face='Courier'>[TBC]</font> phrases:")
p("&nbsp;&nbsp;&mdash; &quot;roughly a third&quot; &rarr; the actual filtered "
  "percentage from the harness logs.")
p("&nbsp;&nbsp;&mdash; &quot;noticeably easier to classify&quot; &rarr; the real "
  "accuracy delta when comparing synthetic-only vs real-data-included runs.")
p("&nbsp;&nbsp;&mdash; &quot;unpredictable lag&quot; &rarr; the actual "
  "<font face='Courier'>stdev_latency_seconds</font> across your 5 runs.")
p("&bull; Record a 2&ndash;3 minute demo video of the full pipeline running. Save "
  "into <font face='Courier'>demo/</font>.")
p("&bull; Take screenshots of: SQLite db open in DB Browser, Gemini API call in "
  "the console logs, Slack message in your workspace, and "
  "<font face='Courier'>results.csv</font> in a spreadsheet. Use these in slides "
  "7&ndash;9.")
p("&bull; Push the repo to GitHub and link from &sect;5.2 of the report.")

story.append(PageBreak())

# =====================================================================
# Section 2: How each code file works
# =====================================================================

h1("2. How each code file works")

p("The repository follows the four-stage pipeline: scrape &rarr; load &rarr; "
  "classify &rarr; deliver. Each stage lives in its own package. The KPI harness "
  "is the orchestrator; everything else is library code.")

# config/

h2("config/")

h3("config/settings.py")
p("Loads <font face='Courier'>.env</font> via "
  "<font face='Courier'>python-dotenv</font> and exposes a frozen "
  "<font face='Courier'>Settings</font> dataclass: API keys, model name, db path, "
  "log level, scraper base URL, and watchlist path. A module-level "
  "<font face='Courier'>settings</font> singleton is imported by every other "
  "module &mdash; no module re-reads <font face='Courier'>.env</font>. The helper "
  "<font face='Courier'>configure_logging()</font> wires Python's stdlib logger "
  "to <font face='Courier'>LOG_LEVEL</font>.")

h3("config/watchlist.json")
p("The 20-company priority list: 10 Tier&nbsp;A active clients, 7 Tier&nbsp;B "
  "prospects, 3 Tier&nbsp;C indicators &mdash; drawn from the Easy Skill brief "
  "(BHP, Rio Tinto, Newmont/Lihir, Barrick/Porgera, TotalEnergies, ExxonMobil, "
  "Glencore, Downer, Monadelphous, Ok&nbsp;Tedi at Tier&nbsp;A; Vale, Eramet, "
  "Saipem, Technip, Subsea7, Aurecon, Vinci at Tier&nbsp;B; Thales, Safran, "
  "Eiffage at Tier&nbsp;C). Each entry carries a sector, notes, and an "
  "<font face='Courier'>aliases</font> array used by the fuzzy matcher.")

# loader/

h2("loader/")

h3("loader/schema.sql")
p("DDL for two tables. The <font face='Courier'>signals</font> table mirrors the "
  "production BigQuery schema: <font face='Courier'>signal_id</font> (UUID), "
  "source metadata, geography, sector, watchlist tier, signal category, review "
  "cycle, raw content, classification notes, "
  "<font face='Courier'>is_new_prospect</font> flag, and "
  "<font face='Courier'>classified_at</font> timestamp. A unique partial index "
  "on <font face='Courier'>source_url</font> enforces dedupe at the database "
  "layer. The <font face='Courier'>watchlist</font> table seeds from the JSON "
  "file at init time.")

h3("loader/ingest.py")
p("Two public functions:")
p("&bull; <font face='Courier'>init_db(db_path, watchlist_path=None)</font> "
  "&mdash; runs the schema script and seeds the watchlist. Idempotent ("
  "<font face='Courier'>INSERT OR REPLACE</font>).")
p("&bull; <font face='Courier'>ingest(records, db_path)</font> &mdash; takes the "
  "scraper's list of dicts, generates UUIDs for missing "
  "<font face='Courier'>signal_id</font>, inserts into "
  "<font face='Courier'>signals</font>, and skips duplicates that violate the "
  "<font face='Courier'>source_url</font> unique index. Returns the inserted "
  "count.")
p("A private <font face='Courier'>wipe_signals()</font> is used by the KPI "
  "harness between runs.")

# agents/

h2("agents/")

h3("agents/prompts.py")
p("Three constants:")
p("&bull; <font face='Courier'>SYSTEM_PROMPT</font> &mdash; defines the Signal "
  "Analyst's role, the five sectors, six signal categories, three review cycles, "
  "and the JSON output schema. Includes explicit alias-resolution guidance "
  "(&quot;EMPNG&rarr;ExxonMobil&quot;, &quot;OTML&rarr;Ok Tedi&quot;).")
p("&bull; <font face='Courier'>CLASSIFY_USER_PROMPT_TEMPLATE</font> &mdash; an "
  "f-string that injects the comma-separated watchlist canonical names plus the "
  "raw posting text.")
p("&bull; <font face='Courier'>BLOCKLIST_KEYWORDS</font> &mdash; ~30 substrings "
  "that pre-filter obvious non-Easy-Skill roles before any LLM call (marketing "
  "manager, hospitality, retail, accounting, childcare, etc.). Filtered counts "
  "are logged so they can be quoted in &sect;5.2.")

h3("agents/signal_analyst.py")
p("The heart of the PoC. Public function "
  "<font face='Courier'>classify_pending(db_path, batch_size=20, gemini_caller=None)</font>. "
  "What it does, in order:")
p("1. Opens SQLite, loads the watchlist (with aliases) into memory.")
p("2. Selects up to <font face='Courier'>batch_size</font> rows where "
  "<font face='Courier'>classified_at IS NULL</font>, oldest first.")
p("3. For each row, runs the pre-filter: drops if "
  "<font face='Courier'>raw_content</font> &lt;&nbsp;50 chars or matches the "
  "blocklist. Filtered rows still get a <font face='Courier'>classified_at</font> "
  "stamp (so they don't reappear) and "
  "<font face='Courier'>analysis_notes</font> records the reason.")
p("4. For surviving rows, builds the user prompt and calls Gemini via "
  "<font face='Courier'>_call_with_retry</font>. The caller is injected ("
  "<font face='Courier'>gemini_caller=...</font>) so tests can swap in a fake. "
  "Real callers use the <font face='Courier'>google-genai</font> SDK with "
  "<font face='Courier'>response_mime_type='application/json'</font> + a strict "
  "JSON schema, so the model is forced to return parseable structured output. "
  "Retries: exponential backoff starting at 8&nbsp;s, up to 5 attempts on "
  "<font face='Courier'>429 RESOURCE_EXHAUSTED</font>.")
p("5. Validates and coerces the JSON via "
  "<font face='Courier'>_coerce_classification</font> &mdash; if the model returns "
  "an off-schema enum, falls back to safe defaults rather than crashing.")
p("6. Calls <font face='Courier'>fuzzy_match_watchlist</font> "
  "(<font face='Courier'>rapidfuzz.WRatio</font>, threshold 85) on the LLM's "
  "<font face='Courier'>watchlist_match</font> string to map it onto a canonical "
  "watchlist company. Resolves the tier from the canonical entry. If no match, "
  "leaves <font face='Courier'>watchlist_tier</font> NULL and respects the LLM's "
  "<font face='Courier'>is_new_prospect</font> flag.")
p("7. Updates the row with the resolved company, sector, category, cycle, tier, "
  "prospect flag, reasoning note, and "
  "<font face='Courier'>classified_at</font>. Sleeps "
  "<font face='Courier'>INTER_CALL_DELAY_SECONDS</font> (7&nbsp;s) between "
  "successful calls to stay under the rate limit.")
p("8. Returns a Counter dict with totals: "
  "<font face='Courier'>total_pending</font>, "
  "<font face='Courier'>classified</font>, per-category counts, "
  "<font face='Courier'>filtered_too_short</font>, "
  "<font face='Courier'>filtered_blocklist</font>, "
  "<font face='Courier'>errors</font>.")

# delivery/

h2("delivery/")

h3("delivery/digest.py")
p("Builds the Slack-flavoured weekly digest. Public function "
  "<font face='Courier'>build_digest(db_path, since)</font> queries all rows "
  "where <font face='Courier'>classified_at</font> is set and "
  "<font face='Courier'>captured_at &gt;= since</font>. Composes five sections, "
  "in this exact order, matching the MIOS Sample Reports format:")
p("1. Header &mdash; &quot;:large_blue_circle: <b>MIOS Weekly Intelligence "
  "&mdash; Week of &lt;date&gt;</b>&quot;")
p("2. Key Signals This Week &mdash; up to 10 items grouped by inferred geography "
  "(<font face='Courier'>infer_geography()</font> runs a keyword sweep over the "
  "raw text for PNG markers like Lihir, Porgera, Tabubil). Within each geography, "
  "items are ranked: leadership &gt; project &gt; financial &gt; competitive "
  "&gt; hiring_velocity &gt; market_intel.")
p("3. Market Pulse &mdash; auto-generated bullets summarising the dataset: "
  "total signals, top sector, AU/PNG split, review-cycle mix, count of new "
  "prospects.")
p("4. Hiring Velocity &mdash; Top 10 Watchlist Clients &mdash; a code-block "
  "table of watchlist companies by signal count this week.")
p("5. New Names (Not in Watchlist) &mdash; a table of distinct companies where "
  "<font face='Courier'>is_new_prospect=1</font>.")

h3("delivery/slack.py")
p("Single function <font face='Courier'>post_digest(webhook_url, "
  "digest_markdown)</font>. POSTs <font face='Courier'>{&quot;text&quot;: ..., "
  "&quot;mrkdwn&quot;: true}</font> to the incoming webhook with a 15&nbsp;s "
  "timeout. Returns <font face='Courier'>True</font> on HTTP 200, "
  "<font face='Courier'>False</font> otherwise. Never raises &mdash; the brief "
  "requires the pipeline to keep running if Slack is down.")

# scraper/

h2("scraper/")

h3("scraper/pngworkforce.py")
p("Two layers, separated for testability:")
p("&bull; <font face='Courier'>parse_listing(html, source_url, base_url)</font> "
  "&mdash; pure parser. Uses BeautifulSoup to look for common job-card patterns "
  "(<font face='Courier'>article.job-listing</font>, "
  "<font face='Courier'>div.job-card</font>, etc.). Falls back to any "
  "<font face='Courier'>&lt;article&gt;</font> with a heading + link. Extracts "
  "title, location, body, resolves relative URLs against the base. Tested "
  "against a saved HTML fixture (<font face='Courier'>tests/fixtures/"
  "pngworkforce_listing.html</font>) so it runs offline.")
p("&bull; <font face='Courier'>scrape(limit=200, base_url=None)</font> &mdash; "
  "live fetcher. Uses crawlee's "
  "<font face='Courier'>BeautifulSoupCrawler</font> with a polite User-Agent. "
  "On any exception &mdash; site change, timeout, malformed HTML &mdash; logs a "
  "warning and returns <font face='Courier'>[]</font>. Per the brief, the "
  "pipeline must continue on synthetic data when scraping fails.")

# evaluation/

h2("evaluation/")

h3("evaluation/kpi_harness.py")
p("The orchestrator. Public function "
  "<font face='Courier'>run_evaluation(db_path, ground_truth_path, runs=5)</font>. "
  "What happens, per run:")
p("1. Wipes the <font face='Courier'>signals</font> table (watchlist preserved).")
p("2. Loads all 80 records from "
  "<font face='Courier'>data/synthetic_postings.jsonl</font>, adapts them to the "
  "ingest record shape, and bulk-inserts.")
p("3. Starts a <font face='Courier'>perf_counter</font>, runs "
  "<font face='Courier'>classify_pending</font>, stops the clock.")
p("4. Calls <font face='Courier'>score_run()</font>: counts a row as correct "
  "only if <i>both</i> "
  "<font face='Courier'>signal_category</font> <i>and</i> "
  "<font face='Courier'>review_cycle</font> match the ground truth. Watchlist "
  "precision = of rows predicted as a watchlist match, fraction where the "
  "predicted company equals the ground-truth company.")
p("5. Records: classification accuracy %, watchlist precision %, latency seconds, "
  "API call count, AU$0.00 (free tier), filter counts, error count.")
p("After all runs, <font face='Courier'>_aggregate()</font> computes the means "
  "and stdev. <font face='Courier'>_write_csv()</font> writes per-run rows + an "
  "aggregate row to <font face='Courier'>results.csv</font>. "
  "<font face='Courier'>_print_kpi_table()</font> prints a markdown table with "
  "Target / Achieved / Status columns matching the &sect;5.2 layout. The CLI "
  "<font face='Courier'>main()</font> then builds the digest and posts it to "
  "Slack unless <font face='Courier'>--no-slack</font> is passed.")

# data/

h2("data/")

h3("data/synthetic_postings.jsonl")
p("80 hand-authored job postings used as the labelled ground-truth set. "
  "Distribution:")
p("&bull; 48 (60%) clearly relevant, watchlist company, clean classification "
  "&mdash; spread across BHP, Rio Tinto, Newmont, Barrick, TotalEnergies, "
  "ExxonMobil, Glencore, Downer, Monadelphous, Ok Tedi, Vale, Eramet, Saipem, "
  "Technip, Subsea7, Aurecon, Vinci, Thales, Safran, Eiffage.")
p("&bull; 20 (25%) clearly irrelevant &mdash; hotel concierge, marketing "
  "manager, retail merchandiser, etc. Should classify as sector "
  "<font face='Courier'>other</font> and not match the watchlist.")
p("&bull; 12 (15%) edge cases &mdash; ambiguous &quot;General Manager &mdash; "
  "Operations&quot; titles at watchlist and non-watchlist companies, alias "
  "variants (Newcrest Lihir, TechnipFMC, EMPNG, OTML), 2 bilingual EN/Tok Pisin "
  "postings, and 4 new-prospect companies (Kumul Petroleum, Hidden Valley Mine, "
  "Coronado Global Resources, Pilbara Minerals).")
p("Each line is JSON with keys: <font face='Courier'>id</font>, "
  "<font face='Courier'>source_url</font>, <font face='Courier'>raw_text</font>, "
  "and the six <font face='Courier'>ground_truth_*</font> labels.")

# tests/

h2("tests/")

h3("tests/test_loader.py (6 tests)")
p("Schema creation, watchlist seeding, idempotency, dedupe by "
  "<font face='Courier'>source_url</font>, empty-content rejection, "
  "<font face='Courier'>wipe_signals</font> behaviour.")

h3("tests/test_signal_analyst.py (15 tests)")
p("Pre-filter (too short, blocklist, pass-through), fuzzy watchlist matching "
  "(exact, alias, typo, unknown, None input), enum-coercion fallback, end-to-end "
  "classification with a fake Gemini caller (happy path, prefilter, alias "
  "resolution, new prospect, error handling).")

h3("tests/test_digest.py (10 tests)")
p("Geography inference, all five sections present, geography grouping order, "
  "exclusion of unclassified rows, "
  "<font face='Courier'>since</font>-window respect, new-prospect table, plus "
  "Slack post (success, non-200, empty URL, RequestException). Slack uses "
  "<font face='Courier'>unittest.mock.patch</font> on "
  "<font face='Courier'>requests.post</font>.")

h3("tests/test_scraper.py (8 tests)")
p("Drives <font face='Courier'>parse_listing</font> against "
  "<font face='Courier'>tests/fixtures/pngworkforce_listing.html</font> &mdash; "
  "no live network. Verifies card detection, URL resolution, title/location "
  "extraction, empty-input handling, and that <font face='Courier'>scrape()</font> "
  "returns <font face='Courier'>[]</font> on invalid URL or crawler exceptions.")

# =====================================================================
# Section 3: Pipeline trace
# =====================================================================

story.append(PageBreak())
h1("3. What happens when you run <font face='Courier'>kpi_harness</font>")

p("End-to-end trace of <font face='Courier'>python -m evaluation.kpi_harness "
  "--runs 1</font>:")

steps = [
    ("Step 1 &mdash; Argparse + logging.",
     "<font face='Courier'>main()</font> parses CLI flags, "
     "<font face='Courier'>configure_logging()</font> sets the root logger to "
     "<font face='Courier'>LOG_LEVEL</font>."),
    ("Step 2 &mdash; Load ground truth.",
     "<font face='Courier'>load_ground_truth()</font> reads "
     "<font face='Courier'>data/synthetic_postings.jsonl</font>, returns 80 dicts."),
    ("Step 3 &mdash; init_db.",
     "<font face='Courier'>loader.ingest.init_db</font> runs "
     "<font face='Courier'>schema.sql</font> against "
     "<font face='Courier'>data/mios.db</font> (creates tables, indexes), then "
     "seeds the watchlist (20 rows). Idempotent &mdash; safe across runs."),
    ("Step 4 &mdash; Wipe signals.",
     "<font face='Courier'>wipe_signals()</font> truncates the signals table. "
     "Watchlist is preserved."),
    ("Step 5 &mdash; Ingest ground truth.",
     "<font face='Courier'>ingest()</font> writes all 80 records into "
     "<font face='Courier'>signals</font> with auto UUIDs and dedupe-by-URL. "
     "<font face='Courier'>classified_at</font> remains NULL on every row."),
    ("Step 6 &mdash; Classification clock starts.",
     "<font face='Courier'>perf_counter()</font> snapshot for the latency KPI."),
    ("Step 7 &mdash; classify_pending.",
     "Loops over the 80 pending signals: pre-filter drops 20 blocklisted rows; "
     "the remaining 60 are sent to Gemini one at a time with a 7&nbsp;s sleep "
     "between calls. Each response is JSON-parsed, schema-coerced, and the "
     "company name is fuzzy-matched against the watchlist. The signals row is "
     "updated with classification + tier + reasoning + "
     "<font face='Courier'>classified_at</font>."),
    ("Step 8 &mdash; Latency captured.",
     "<font face='Courier'>perf_counter()</font> stop. Latency is recorded for "
     "this run."),
    ("Step 9 &mdash; Score the run.",
     "<font face='Courier'>score_run()</font> joins classified rows back to "
     "ground truth on <font face='Courier'>signal_id</font>, computes "
     "classification accuracy and watchlist precision."),
    ("Step 10 &mdash; results.csv.",
     "<font face='Courier'>_write_csv()</font> writes the per-run row + an "
     "aggregate row (means + stdev) to <font face='Courier'>results.csv</font> "
     "at the repo root."),
    ("Step 11 &mdash; KPI table to stdout.",
     "<font face='Courier'>_print_kpi_table()</font> emits the &sect;5.2 "
     "markdown table with Target / Achieved / Status."),
    ("Step 12 &mdash; Build the digest.",
     "<font face='Courier'>delivery.digest.build_digest()</font> queries the "
     "classified rows captured in the last 7&nbsp;days and renders the five "
     "Slack-mrkdwn sections."),
    ("Step 13 &mdash; Post to Slack.",
     "<font face='Courier'>delivery.slack.post_digest()</font> POSTs the digest "
     "to <font face='Courier'>SLACK_WEBHOOK_URL</font>. Logs success/failure but "
     "never raises."),
    ("Step 14 &mdash; Exit.",
     "<font face='Courier'>main()</font> returns 0 if any rows were classified, "
     "1 otherwise."),
]

for title, body in steps:
    h3(title)
    p(body)

story.append(Spacer(1, 12))
note("Quota tip. Gemini 2.5 Flash free tier is approximately 200 requests per "
     "day. One full run uses about 60 calls. Five runs in one day will exhaust "
     "the daily allowance &mdash; either spread runs across days or set "
     "GEMINI_MODEL=gemini-2.5-flash-lite to use the separate quota pool.")

# =====================================================================
# Build
# =====================================================================

doc = SimpleDocTemplate(
    str(OUT_PATH), pagesize=A4,
    leftMargin=2 * cm, rightMargin=2 * cm,
    topMargin=2 * cm, bottomMargin=2 * cm,
    title="MIOS PoC Run Guide",
    author="Easy Skill Australia / MIOS PoC team",
)
doc.build(story)
print(f"wrote {OUT_PATH}")
