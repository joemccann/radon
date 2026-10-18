-- 0097_vix_pc.sql  -  VIX 5-session cumulative put/call ratio.
-- Raw daily put/call volumes plus the single-day ratio. cum5_ratio is
-- NULL for the first WINDOW-1 rows and when the 5-session call sum is 0.
-- source is 'uw'. Recomputed on every run over the full stored series.

CREATE TABLE IF NOT EXISTS vix_pc_history (
  date         TEXT PRIMARY KEY,  -- YYYY-MM-DD session
  put_volume   INTEGER NOT NULL,
  call_volume  INTEGER NOT NULL,
  daily_ratio  REAL,
  cum5_ratio   REAL,
  source       TEXT NOT NULL,     -- 'uw'
  recorded_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vix_pc_history_date
  ON vix_pc_history(date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (97, datetime('now'));
