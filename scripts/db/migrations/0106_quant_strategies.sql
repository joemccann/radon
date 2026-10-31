-- 0106_quant_strategies.sql — systematic strategy runner (docs/quant-strategies.md).
--
-- quant_runs: one row per backtest run per strategy (latest by run_at); the
-- summary columns are what the UI lists, payload holds the full result JSON.
-- quant_targets: the strategy's current target weights (and sleeve notional
-- when a sleeve NAV was supplied), one row per symbol per as_of session.
-- Neither table carries orders: execution is a separate bridge.

CREATE TABLE IF NOT EXISTS quant_runs (
  strategy     TEXT    NOT NULL,          -- registry key, e.g. 'trend_inverse_vol'
  run_at       TEXT    NOT NULL,          -- ISO-8601 UTC
  mode         TEXT    NOT NULL,          -- 'backtest'
  since        TEXT,                      -- first session in the run
  until        TEXT,                      -- last session in the run
  n_sessions   INTEGER NOT NULL,
  n_rebalances INTEGER NOT NULL,
  ann_return   REAL,
  ann_vol      REAL,
  sharpe       REAL,
  max_drawdown REAL,
  payload      TEXT    NOT NULL,          -- full result JSON
  PRIMARY KEY (strategy, run_at)
);

CREATE INDEX IF NOT EXISTS idx_quant_runs_latest ON quant_runs(strategy, run_at DESC);

CREATE TABLE IF NOT EXISTS quant_targets (
  strategy    TEXT NOT NULL,
  as_of       TEXT NOT NULL,              -- session date the weights were computed on
  symbol      TEXT NOT NULL,
  weight      REAL NOT NULL,              -- signed fraction of the sleeve
  notional    REAL,                       -- weight * sleeve NAV, null when no sleeve given
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (strategy, as_of, symbol)
);

CREATE INDEX IF NOT EXISTS idx_quant_targets_latest ON quant_targets(strategy, as_of DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (106, datetime('now'));
