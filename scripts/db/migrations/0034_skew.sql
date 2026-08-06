-- 0034_skew.sql — SKEW indicator: SPX 1-month 25-delta put/call IV ratio,
-- one row per completed session (2023-09-06+, the UW greeks history floor).
--
-- ratio = put_iv_25d / call_iv_25d, both linearly interpolated in delta on
-- the monthly expiry nearest 30 DTE. change = ratio_t - ratio_{t-1}; NULL on
-- the first stored session (no prior ratio to difference against).

CREATE TABLE IF NOT EXISTS skew_history (
  date        TEXT PRIMARY KEY,
  expiry      TEXT NOT NULL,
  dte         INTEGER NOT NULL,
  put_iv      REAL NOT NULL,
  call_iv     REAL NOT NULL,
  ratio       REAL NOT NULL,
  change      REAL,              -- NULL on the first stored session
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skew_history_date ON skew_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (34, datetime('now'));
