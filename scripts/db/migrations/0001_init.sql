-- 0001_init.sql — initial schema for the Radon Turso DB.
--
-- All hot data lives here. JSON columns hold scheduler payloads verbatim;
-- the dashboard reads one row → hydrates one panel. Time-keyed primary
-- keys (taken_at, scan_time) make concurrent writes from two replicas
-- collision-free (each write produces a distinct row).
--
-- Conventions:
--   - Timestamps stored as ISO-8601 TEXT (UTC). lexicographic ordering
--     == chronological ordering, so MAX(taken_at) gives the latest.
--   - JSON payloads stored as TEXT (libSQL has json1 functions if we
--     ever need to query into them).
--   - All-uppercase tag values; case-insensitive primary key on
--     tag_taxonomy(tag) prevents BTC/btc/Btc duplicates.

PRAGMA foreign_keys = ON;

-- ─── Browser-scraped (writers: themarketear scraper laptop-only,
--                      MenthorQ scraper either side depending on mode) ──

CREATE TABLE IF NOT EXISTS posts (
  id          TEXT    PRIMARY KEY,
  title       TEXT    NOT NULL,
  content     TEXT,
  timestamp   TEXT    NOT NULL,
  images      TEXT,           -- JSON array of absolute URLs (https://media.radon.run/...)
  raw_images  TEXT,           -- JSON array of upstream themarketear URLs
  tags        TEXT,           -- JSON array of UPPERCASE tags (final/merged)
  tags_text   TEXT,           -- JSON array (text-tagger output, pre-merge)
  tags_vision TEXT,           -- JSON array (vision-tagger output, pre-merge)
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_timestamp ON posts(timestamp DESC);

CREATE TABLE IF NOT EXISTS tag_taxonomy (
  tag         TEXT    PRIMARY KEY COLLATE NOCASE,
  created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS menthorq_cta (
  date        TEXT    PRIMARY KEY,        -- YYYY-MM-DD
  payload     TEXT    NOT NULL,           -- JSON (existing cache_meta + tables shape)
  fetched_at  TEXT    NOT NULL
);

-- ─── Hot data (writers: schedulers — laptop launchd OR Hetzner systemd) ──

CREATE TABLE IF NOT EXISTS cri_snapshots (
  date        TEXT    NOT NULL,           -- ET trading day, YYYY-MM-DD
  taken_at    TEXT    NOT NULL,           -- ISO-8601 UTC
  payload     TEXT    NOT NULL,
  PRIMARY KEY (date, taken_at)
);
CREATE INDEX IF NOT EXISTS idx_cri_latest ON cri_snapshots(date DESC, taken_at DESC);

CREATE TABLE IF NOT EXISTS gex_snapshots (
  ticker      TEXT    NOT NULL,
  scan_time   TEXT    NOT NULL,
  payload     TEXT    NOT NULL,
  PRIMARY KEY (ticker, scan_time)
);
CREATE INDEX IF NOT EXISTS idx_gex_latest ON gex_snapshots(ticker, scan_time DESC);

CREATE TABLE IF NOT EXISTS vcg_snapshots (
  scan_time   TEXT    PRIMARY KEY,
  payload     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS journal (
  trade_id    TEXT    PRIMARY KEY,
  payload     TEXT    NOT NULL,
  filled_at   TEXT,
  written_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_filled ON journal(filled_at DESC);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  taken_at    TEXT    PRIMARY KEY,
  payload     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS discover_snapshots (
  scan_time   TEXT    PRIMARY KEY,
  payload     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS analyst_ratings (
  ticker      TEXT    NOT NULL,
  fetched_at  TEXT    NOT NULL,
  payload     TEXT    NOT NULL,
  PRIMARY KEY (ticker, fetched_at)
);
CREATE INDEX IF NOT EXISTS idx_analyst_latest ON analyst_ratings(ticker, fetched_at DESC);

CREATE TABLE IF NOT EXISTS oi_changes (
  scan_time   TEXT    PRIMARY KEY,
  payload     TEXT    NOT NULL
);

-- ─── Operational ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS service_health (
  service                  TEXT  PRIMARY KEY,
  state                    TEXT  NOT NULL,    -- 'ok' | 'syncing' | 'error' | 'paused'
  last_attempt_started_at  TEXT,
  last_attempt_finished_at TEXT,
  last_error               TEXT,              -- JSON
  updated_at               TEXT  NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT    NOT NULL
);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, datetime('now'));
