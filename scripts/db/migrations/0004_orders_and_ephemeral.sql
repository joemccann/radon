-- 0004_orders_and_ephemeral.sql
-- Phase 3 (orders) + Phase 4 (ephemeral) of the Turso source-of-truth
-- migration. Adds per-row tables for the highest-cadence + per-host
-- state previously held in non-atomic snapshots.
--
-- Why per-row instead of single-snapshot blobs:
--   - open_orders / executed_orders are written sub-second on busy days;
--     a per-row INSERT OR REPLACE keyed by permId / execId scales without
--     read-during-write torn-blob risk from monolithic order snapshots.
--   - daemon_state, app_config, watchlist, ticker_lookup_cache are
--     queried per-key from many call sites; tables let us index + filter
--     instead of parsing the whole blob each time.

-- ── Orders (Phase 3) ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS open_orders (
  perm_id     INTEGER PRIMARY KEY,
  payload     TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS executed_orders (
  exec_id     TEXT    PRIMARY KEY,
  perm_id     INTEGER,
  payload     TEXT    NOT NULL,
  fill_time   TEXT    NOT NULL,
  recorded_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS executed_orders_fill_time_idx
  ON executed_orders (fill_time DESC);

CREATE INDEX IF NOT EXISTS executed_orders_perm_id_idx
  ON executed_orders (perm_id);

-- ── Ephemeral state (Phase 4) ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS daemon_state (
  handler      TEXT PRIMARY KEY,
  last_run     TEXT,
  last_status  TEXT,
  last_error   TEXT,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_config (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- watchlist — one row per ticker. The previous monolithic snapshot was ~590KB;
-- per-row lets per-sector / per-source filtering happen in SQL.
CREATE TABLE IF NOT EXISTS watchlist (
  ticker      TEXT PRIMARY KEY,
  sector      TEXT,
  source      TEXT,
  payload     TEXT,
  last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticker_lookup_cache (
  query       TEXT PRIMARY KEY,
  result      TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  cached_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_log (
  snapshot_at TEXT PRIMARY KEY,
  payload     TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
  VALUES (4, datetime('now'));
