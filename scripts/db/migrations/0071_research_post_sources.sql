-- Private research provenance is separate from the legacy Market Ear rows.
CREATE TABLE IF NOT EXISTS research_post_sources (
  post_id TEXT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
  provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json))
);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (71, datetime('now'));
