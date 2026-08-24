-- MIOS schema. One file serves both engines: Neon PostgreSQL in deployment and
-- SQLite locally, written in the dialect subset they share.
--
-- Portability constraints to preserve when editing:
--   * CREATE TABLE / INDEX IF NOT EXISTS  - both (Postgres 9.5+)
--   * partial unique index (WHERE ...)    - both
--   * TEXT timestamps in ISO-8601 UTC     - sorts correctly as a string, so no
--                                           timestamptz/TEXT divergence
--   * INTEGER for booleans (0/1)          - avoids BOOLEAN vs INTEGER mismatch
-- Anything outside that subset needs two schemas; don't add it casually.

CREATE TABLE IF NOT EXISTS signals (
    signal_id          TEXT PRIMARY KEY,
    source_type        TEXT NOT NULL,
    source_name        TEXT NOT NULL,
    source_url         TEXT,
    captured_at        TEXT NOT NULL,
    geography          TEXT NOT NULL,
    sector             TEXT,
    company_name       TEXT,
    watchlist_tier     TEXT,
    signal_category    TEXT,
    review_cycle       TEXT,
    raw_content        TEXT NOT NULL,
    analysis_notes     TEXT,
    is_new_prospect    INTEGER DEFAULT 0,
    classified_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_company  ON signals(company_name);
CREATE INDEX IF NOT EXISTS idx_signals_captured ON signals(captured_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_source_url ON signals(source_url) WHERE source_url IS NOT NULL;

CREATE TABLE IF NOT EXISTS watchlist (
    company_name       TEXT PRIMARY KEY,
    tier               TEXT NOT NULL,
    sector             TEXT,
    notes              TEXT,
    aliases            TEXT
);

-- Mode Push: consultant/candidate profiles the BD team submits, either by
-- uploading a CV or by filling the form. MIOS matches these against the hiring
-- signals in `signals` to find companies that need someone with these skills.
--
-- Data minimisation is deliberate. These are real people, so only the fields
-- that matching actually consumes are stored, plus enough identity for the BD
-- team to know whose profile it is. The uploaded CV itself is NOT retained:
-- it is parsed in memory and discarded, so the document never lands on disk or
-- in a backup. Contact details are optional for the same reason.
CREATE TABLE IF NOT EXISTS candidate_profiles (
    profile_id         TEXT PRIMARY KEY,
    full_name          TEXT NOT NULL,
    email              TEXT,
    phone              TEXT,
    current_title      TEXT,
    sector             TEXT,
    years_experience   INTEGER,
    region             TEXT,
    -- JSON array, same convention as watchlist.aliases: portable across both
    -- engines without needing a JSON column type.
    skills             TEXT,
    availability       TEXT,
    -- 'cv_upload' or 'manual_form' — the BD team reviews parsed CVs before save,
    -- so this records how the data originally arrived, not how much to trust it.
    intake_source      TEXT NOT NULL,
    source_filename    TEXT,
    notes              TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_profiles_created ON candidate_profiles(created_at);
CREATE INDEX IF NOT EXISTS idx_profiles_sector  ON candidate_profiles(sector);

-- Mode Publish: quarterly client-facing reports.
--
-- The report is assembled from the signals already in this database, then a
-- human works through it section by section. That review is not a nicety —
-- §8.3 of the project spec requires it to be architecturally enforced, which is
-- why there is no column recording an automated send and no endpoint that
-- performs one. `approved_by` records the person who signed it off, and a
-- report cannot reach 'approved' while any section is still pending.
CREATE TABLE IF NOT EXISTS reports (
    report_id        TEXT PRIMARY KEY,
    -- '2026-Q3'. Reports are per quarter, but a quarter may have several drafts:
    -- regenerating never overwrites one somebody has already been editing.
    quarter          TEXT NOT NULL,
    title            TEXT NOT NULL,
    -- 'draft' -> 'approved'. Nothing beyond 'approved' exists on purpose:
    -- distribution happens outside MIOS, by a person.
    status           TEXT NOT NULL,
    generated_at     TEXT NOT NULL,
    -- How much evidence the prose stands on, so a thin quarter is visible
    -- rather than reading with the same confidence as a full one.
    signals_analysed INTEGER NOT NULL,
    window_from      TEXT,
    window_to        TEXT,
    approved_at      TEXT,
    approved_by      TEXT,
    -- 'computed' or 'gemini'. Which produced the wording that shipped — not a
    -- preference, a record. A reader deserves to know whether a language model
    -- touched the prose in a document going to clients.
    prose_source     TEXT NOT NULL DEFAULT 'computed',
    -- Why the computed wording was kept, when it was: no key, quota exhausted,
    -- or a rewrite that invented figures.
    prose_note       TEXT
);

CREATE TABLE IF NOT EXISTS report_sections (
    section_id     TEXT PRIMARY KEY,
    report_id      TEXT NOT NULL,
    position       INTEGER NOT NULL,
    heading        TEXT NOT NULL,
    body           TEXT NOT NULL,
    -- What the generator originally wrote. Kept alongside `body` so an editor
    -- can see what they changed, and so "edited by a human" is a fact rather
    -- than an assumption.
    generated_body TEXT NOT NULL,
    -- 'generated' or 'manual'. A manual section starts empty because the data
    -- cannot support it — a forward-looking outlook is a judgement, not a count.
    source         TEXT NOT NULL,
    approved       INTEGER NOT NULL DEFAULT 0,
    approved_at    TEXT,
    approved_by    TEXT,
    edited_at      TEXT,
    -- The deterministic prose, always. `generated_body` may hold a Gemini
    -- rewrite of it; keeping both means a reviewer can see exactly what the
    -- model changed rather than trusting that it only changed the wording.
    computed_body  TEXT
);

CREATE INDEX IF NOT EXISTS idx_reports_quarter   ON reports(quarter);
CREATE INDEX IF NOT EXISTS idx_sections_report   ON report_sections(report_id, position);
