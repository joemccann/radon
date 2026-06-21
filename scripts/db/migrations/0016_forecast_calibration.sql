-- 0016_forecast_calibration.sql — Chronos-2 forecasting: per-ticker
-- calibration report rows produced by the repeatable backtest harness.
--
-- One row per (ticker, metric, scan_time). payload is the full
-- run_backtest() output (baseline + chronos walk-forward + verdict);
-- the scalar columns are the headline numbers lifted out for indexed
-- queries / dashboards.

CREATE TABLE IF NOT EXISTS forecast_calibration (
  ticker                TEXT NOT NULL,
  metric                TEXT NOT NULL,
  scan_time             TEXT NOT NULL,
  engine                TEXT,
  series_len            INTEGER,
  mean_pinball_chronos  REAL,
  mean_pinball_baseline REAL,
  verdict               TEXT,
  payload               TEXT NOT NULL,
  PRIMARY KEY (ticker, metric, scan_time)
);

CREATE INDEX IF NOT EXISTS idx_forecast_calibration_ticker
  ON forecast_calibration(ticker, metric, scan_time);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (16, datetime('now'));
