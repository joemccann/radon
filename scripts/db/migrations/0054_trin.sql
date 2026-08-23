-- 0054_trin.sql — TRIN indicator: five-minute RTH samples of the NYSE Arms
-- Index (hourly bars + MA(10) are derived at read time) and StockCharts $TRIN
-- daily closes for long-history context.

CREATE TABLE IF NOT EXISTS trin_samples (
  ts           TEXT PRIMARY KEY,      -- UTC ISO, 5-minute sample
  session_date TEXT NOT NULL,         -- ET session date
  trin         REAL NOT NULL,
  adv          INTEGER,
  dec          INTEGER,
  up_vol       REAL,
  down_vol     REAL,
  source       TEXT NOT NULL,
  recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trin_samples_ts_desc ON trin_samples (ts DESC);
CREATE TABLE IF NOT EXISTS trin_daily (
  date        TEXT PRIMARY KEY,
  close       REAL NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trin_daily_date_desc ON trin_daily (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (54, datetime('now'));
