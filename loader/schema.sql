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
