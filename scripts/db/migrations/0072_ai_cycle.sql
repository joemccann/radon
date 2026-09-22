-- Compact GET snapshot plus durable observation/raw tables.
-- Observations were previously created at collector runtime; the API snapshot
-- is the only payload GET /ai-cycle may read.
CREATE TABLE IF NOT EXISTS ai_cycle_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL UNIQUE,
  available_at TEXT NOT NULL,
  period_end TEXT NOT NULL,
  identity TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_cycle_observations_available ON ai_cycle_observations(available_at);
CREATE INDEX IF NOT EXISTS ai_cycle_observations_identity ON ai_cycle_observations(identity, available_at, period_end);
CREATE TABLE IF NOT EXISTS ai_cycle_source_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  checked_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_cycle_raw (
  hash TEXT PRIMARY KEY,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_cycle_api_snapshot (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  generated_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (72, datetime('now'));
