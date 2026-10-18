CREATE TABLE IF NOT EXISTS newsfeed_explanations (
    post_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    explanation TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    has_image INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (post_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_newsfeed_explanations_created
    ON newsfeed_explanations (created_at DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (96, datetime('now'));
