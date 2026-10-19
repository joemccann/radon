-- VIX model spread; source and calculation: docs/indicators/vix-model-spread.md
CREATE TABLE IF NOT EXISTS vix_model_spread_history (
  date TEXT PRIMARY KEY,
  vix REAL NOT NULL,
  vixeq REAL NOT NULL,
  cor1m REAL NOT NULL,
  model_vix REAL NOT NULL,
  spread REAL NOT NULL,
  mean_20 REAL,
  std_20 REAL,
  upper_20 REAL,
  lower_20 REAL,
  z_20 REAL,
  mean_252 REAL,
  std_252 REAL,
  upper_252 REAL,
  lower_252 REAL,
  z_252 REAL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vix_model_spread_history_date ON vix_model_spread_history(date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (98, datetime('now'));
