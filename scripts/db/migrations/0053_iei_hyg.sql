-- 0053_iei_hyg.sql — IEI/HYG indicator: daily IEI and HYG closes (ratio is
-- derived, not stored) with the ICE US Dollar Index as a nullable overlay.
-- Sources IB -> UW -> Yahoo, 2007-04-11+ (HYG inception).

CREATE TABLE IF NOT EXISTS iei_hyg_history (
  date        TEXT PRIMARY KEY,
  iei_close   REAL NOT NULL,
  hyg_close   REAL NOT NULL,
  dxy_close   REAL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_iei_hyg_history_date_desc ON iei_hyg_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (53, datetime('now'));
