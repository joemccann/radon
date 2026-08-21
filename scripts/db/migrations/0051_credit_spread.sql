-- 0051_credit_spread.sql — CREDIT indicator: daily HYG vs S&P 500 closes
-- (Yahoo Finance, 2007-04-11+). Divergence is equities up over 168
-- sessions while HYG is down. ICE CCC OAS is not stored.

CREATE TABLE IF NOT EXISTS credit_spread_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD session
  hyg_close   REAL NOT NULL,
  spx_close   REAL NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_credit_spread_history_date
  ON credit_spread_history(date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (51, datetime('now'));
