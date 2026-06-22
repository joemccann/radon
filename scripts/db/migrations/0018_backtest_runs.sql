-- 0018_backtest_runs.sql — F12 strategy backtester + walk-forward harness.
--
-- One row per backtest run: the strategy key, the run parameters, the headline
-- metrics (denormalised for cheap listing/sorting), and the full result JSON
-- payload (trades + equity curve + every metric). The JSON mirrors the disk
-- fallback at data/backtest/{strategy}.json.

CREATE TABLE IF NOT EXISTS backtest_runs (
  strategy   TEXT    NOT NULL,           -- registry key, e.g. 'cri'
  run_at     TEXT    NOT NULL,           -- ISO-8601 UTC
  horizon    INTEGER NOT NULL,           -- forward-return horizon in sessions
  n_trades   INTEGER NOT NULL,
  sharpe     REAL,
  sortino    REAL,
  calmar     REAL,
  max_drawdown REAL,
  hit_rate   REAL,
  expectancy REAL,
  payload    TEXT    NOT NULL,           -- full result JSON
  PRIMARY KEY (strategy, run_at)
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_latest
  ON backtest_runs(strategy, run_at DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (18, datetime('now'));
