-- One row per document the research intake has reviewed: what happened
-- (published | held | dropped), the reason codes, and the drafts it rejected.
-- The authoritative audit stays in the private evidence directory on the
-- worker host. This mirror exists so the operator can review a daily sample
-- of held and dropped documents in the app and vote on them
-- (research_feedback target = 'held', keyed by work_key).
CREATE TABLE IF NOT EXISTS research_outcomes (
    work_key TEXT PRIMARY KEY,
    file_id TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT 'unknown',
    series TEXT NOT NULL DEFAULT '',
    doc_type TEXT NOT NULL DEFAULT '',
    folder_date TEXT NOT NULL DEFAULT '',
    document_date TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL CHECK (outcome IN ('published', 'held', 'dropped')),
    reason_codes TEXT NOT NULL DEFAULT '[]',
    drafts_json TEXT NOT NULL DEFAULT '[]',
    posts INTEGER NOT NULL DEFAULT 0,
    pipeline TEXT NOT NULL DEFAULT 'v2',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_outcomes_review ON research_outcomes (outcome, folder_date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (79, datetime('now'));
