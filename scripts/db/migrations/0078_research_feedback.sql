-- Operator feedback on research feed items and on held/dropped documents.
-- Append-only: a new vote is a new row and the latest row per target wins
-- ('clear' withdraws). A latest 'down' on a post hides it from the feed. The
-- post row itself is never altered, so retraction is reversible and the
-- label history survives. snapshot_json freezes the identity the vote was
-- cast against (title, tags, publisher, series, doc type, pipeline) so the
-- nightly learner trains on what the operator actually saw.
CREATE TABLE IF NOT EXISTS research_feedback (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL CHECK (target IN ('post', 'held')),
    post_id TEXT,
    work_key TEXT,
    file_id TEXT,
    vote TEXT NOT NULL CHECK (vote IN ('up', 'down', 'clear')),
    reasons TEXT NOT NULL DEFAULT '[]',
    comment TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    CHECK ((target = 'post' AND post_id IS NOT NULL) OR (target = 'held' AND work_key IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_research_feedback_post ON research_feedback (post_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_feedback_work ON research_feedback (work_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_feedback_created ON research_feedback (created_at DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (78, datetime('now'));
