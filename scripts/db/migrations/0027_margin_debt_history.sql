-- 0027_margin_debt_history.sql — Margin Debt Acceleration Indicator: monthly
-- FINRA margin statistics (Jan 1997+) spliced behind the legacy NYSE
-- member-firm series (1959-1996, one-time Wayback backfill).
--
-- One row per calendar month. level is the RAW published debit balance in
-- $ millions — the ~4% member-firm-scope step at the Jan 1997 splice is
-- preserved and disambiguated by source ('nyse_legacy' | 'finra'); display
-- ratio-adjustment happens at read time. level_yoy_pct is NULL for the first
-- 12 months of history and for the 12 months whose lookback crosses the
-- splice.

CREATE TABLE IF NOT EXISTS margin_debt_history (
  date               TEXT PRIMARY KEY,  -- YYYY-MM (month-end reference)
  level              REAL NOT NULL,     -- debit balances, $ millions, raw
  level_yoy_pct      REAL,
  free_credit_cash   REAL,              -- cash-account free credit, $ millions
  free_credit_margin REAL,              -- margin-account free credit, $ millions
  source             TEXT NOT NULL,     -- 'nyse_legacy' | 'finra'
  recorded_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_margin_debt_history_date
  ON margin_debt_history(date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (27, datetime('now'));
