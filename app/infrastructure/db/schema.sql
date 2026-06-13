-- arXiv Paper Research — SQLite schema
-- All timestamps are stored as UTC ISO 8601 strings.
-- All list fields are stored as JSON text.
-- All boolean fields are stored as INTEGER (0/1).
--
-- Foreign key constraints enforce referential integrity:
--   paper_versions  → papers        ON DELETE CASCADE
--   paper_statuses  → papers        ON DELETE CASCADE
--   sync_runs       → subscriptions ON DELETE RESTRICT

CREATE TABLE IF NOT EXISTS papers (
    arxiv_id          TEXT PRIMARY KEY,
    latest_version    INTEGER NOT NULL,
    title             TEXT NOT NULL,
    abstract          TEXT NOT NULL,
    authors_json      TEXT NOT NULL,
    primary_category  TEXT NOT NULL,
    categories_json   TEXT NOT NULL,
    published_at      TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    pdf_url           TEXT,
    abs_url           TEXT NOT NULL,
    comment           TEXT,
    journal_ref       TEXT,
    doi               TEXT,
    created_at        TEXT NOT NULL,
    synced_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_primary_category ON papers (primary_category);
CREATE INDEX IF NOT EXISTS idx_papers_updated_at ON papers (updated_at);
CREATE INDEX IF NOT EXISTS idx_papers_published_at ON papers (published_at);

CREATE TABLE IF NOT EXISTS paper_versions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id          TEXT NOT NULL
                      REFERENCES papers(arxiv_id) ON DELETE CASCADE,
    version           INTEGER NOT NULL,
    title             TEXT NOT NULL,
    abstract          TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    raw_payload_json  TEXT,
    UNIQUE (arxiv_id, version)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    enabled                 INTEGER NOT NULL,
    categories_json         TEXT NOT NULL,
    include_keywords_json   TEXT NOT NULL,
    exclude_keywords_json   TEXT NOT NULL,
    authors_json            TEXT NOT NULL,
    query_text              TEXT,
    sync_interval_minutes   INTEGER NOT NULL,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    last_synced_at          TEXT
);

CREATE TABLE IF NOT EXISTS paper_statuses (
    arxiv_id      TEXT PRIMARY KEY
                  REFERENCES papers(arxiv_id) ON DELETE CASCADE,
    is_starred    INTEGER NOT NULL DEFAULT 0,
    is_read       INTEGER NOT NULL DEFAULT 0,
    is_hidden     INTEGER NOT NULL DEFAULT 0,
    rating        INTEGER,
    note          TEXT,
    tags_json     TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id                TEXT PRIMARY KEY,
    subscription_id   TEXT NOT NULL
                      REFERENCES subscriptions(id) ON DELETE RESTRICT,
    trigger_type      TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL,
    fetched_count     INTEGER NOT NULL DEFAULT 0,
    inserted_count    INTEGER NOT NULL DEFAULT 0,
    updated_count     INTEGER NOT NULL DEFAULT 0,
    error_message     TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
