-- 0052_ivrank.sql — IV RANK indicator: SPY 30-day implied volatility ranked
-- against its trailing 252-session range. iv is the raw annualized 30d IV
-- close (decimal, e.g. 0.1220). iv_rank / iv_pct are NULL for the first
-- RANK_WINDOW-1 rows of history and for degenerate windows. source records
-- which feed produced the row ('ib' primary, 'uw' fallback).

CREATE TABLE IF NOT EXISTS ivrank_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD session date
  iv          REAL NOT NULL,      -- 30d ATM IV close, annualized decimal
  iv_rank     REAL,               -- 0..100, NULL pre-window/degenerate
  iv_pct      REAL,               -- 0..100, NULL pre-window
  source      TEXT NOT NULL,      -- 'ib' | 'uw'
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ivrank_history_date ON ivrank_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (52, datetime('now'));
