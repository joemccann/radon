-- Explicit per-image provider credits supplied by Market Ear.
ALTER TABLE posts ADD COLUMN image_sources TEXT NOT NULL DEFAULT '{}';
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (73, datetime('now'));
