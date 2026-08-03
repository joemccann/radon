-- 0032_yield_curve.sql — 10Y-2Y Treasury Yield Curve Indicator: daily
-- constant-maturity par yields (treasury.gov, 1990+) with the 10Y minus 2Y
-- spread the CURVE regime tab charts against the S&P 500.
--
-- One row per published business day. y3m is nullable — the 3-month tenor
-- has whole-year and mid-year gaps in the official series; y2 and y10 are
-- present in every year 1990+ (rows missing either are skipped at ingest).

CREATE TABLE IF NOT EXISTS yield_curve_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD business day
  y3m         REAL,               -- 3-month CMT par yield, percent
  y2          REAL NOT NULL,      -- 2-year
  y10         REAL NOT NULL,      -- 10-year
  spread      REAL NOT NULL,      -- y10 - y2, percentage points
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_yield_curve_history_date
  ON yield_curve_history(date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (32, datetime('now'));
