-- Host-tagged Liquid Compute public GPU index. Third venue; never splice
-- onto gpu-rental / Silicon Data / matched asking-price cohorts.
-- Idempotent on (source, series_id, date); date and vintage are publisher asOf.
CREATE TABLE IF NOT EXISTS liquidcompute_index (
  date TEXT NOT NULL,
  source TEXT NOT NULL,
  series_id TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  vintage TEXT NOT NULL,
  label TEXT,
  fetched_at TEXT NOT NULL,
  raw_hash TEXT NOT NULL,
  PRIMARY KEY (source, series_id, date)
);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (74, datetime('now'));
