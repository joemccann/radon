-- 0015_forecast_snapshots.sql — Chronos-2 forecasting: persisted quantile
-- forecast snapshots.
--
-- One row per (ticker, metric, scan_time). payload is the JSON-serialised
-- QuantileForecast.to_dict() output (quantile levels, per-step values, median).

CREATE TABLE IF NOT EXISTS forecast_snapshots (
  ticker    TEXT NOT NULL,
  metric    TEXT NOT NULL,
  scan_time TEXT NOT NULL,
  horizon   INTEGER NOT NULL,
  model_id  TEXT NOT NULL,
  payload   TEXT NOT NULL,
  PRIMARY KEY (ticker, metric, scan_time)
);

CREATE INDEX IF NOT EXISTS idx_forecast_snapshots_ticker_metric
  ON forecast_snapshots(ticker, metric, scan_time);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (15, datetime('now'));
