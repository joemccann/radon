-- 0039_skew2d.sql — SKEW 2D indicator: two-session change in the SPX 1M
-- 25-delta put/call IV ratio (derived from skew_history; no network).
--
-- change = ratio_t - ratio_{t-2}; NULL on the first two stored sessions.

CREATE TABLE IF NOT EXISTS skew2d_history (
  date        TEXT PRIMARY KEY,
  expiry      TEXT NOT NULL,
  dte         INTEGER NOT NULL,
  put_iv      REAL NOT NULL,
  call_iv     REAL NOT NULL,
  ratio       REAL NOT NULL,
  change      REAL,              -- NULL on first two stored sessions
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skew2d_history_date ON skew2d_history (date DESC);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (39, datetime('now'));
