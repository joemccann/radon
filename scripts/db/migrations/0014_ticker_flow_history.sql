-- 0014_ticker_flow_history.sql — Chronos-2 forecasting: per-ticker daily
-- dark-pool flow series.
--
-- One row per (ticker, date) accruing the scanner/discover flow signal so the
-- forecasting engine has a numeric history to condition on. flow_strength is
-- the primary target metric; the rest are kept for covariates / context.

CREATE TABLE IF NOT EXISTS ticker_flow_history (
  ticker        TEXT NOT NULL,
  date          TEXT NOT NULL,
  flow_strength REAL,
  dp_direction  TEXT,
  buy_ratio     REAL,
  num_prints    INTEGER,
  total_premium REAL,
  total_volume  INTEGER,
  recorded_at   TEXT NOT NULL,
  PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ticker_flow_history_ticker_date
  ON ticker_flow_history(ticker, date);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (14, datetime('now'));
