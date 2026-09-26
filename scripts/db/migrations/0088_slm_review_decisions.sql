-- Decisions and cursor only. Packet text and image URLs stay on the reviewer's device.
CREATE TABLE IF NOT EXISTS slm_review_decisions (
  run_id TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  decisions_json TEXT NOT NULL DEFAULT '[]',
  cursor_index INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, reviewer),
  CHECK (cursor_index BETWEEN 0 AND 199)
);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (88, datetime('now'));
