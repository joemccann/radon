-- Hard triage rules the research worker PROPOSES from operator votes
-- (a series, publisher or document type rejected repeatedly and never
-- approved). Nothing here changes triage until the operator approves it in
-- the app. The worker only ever inserts a proposal or refreshes its counts.
-- It never writes status, so a decision is the operator's alone and survives
-- every later re-proposal.
CREATE TABLE IF NOT EXISTS research_rule_proposals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('series_deny', 'publisher_deny', 'doc_type_drop')),
    key TEXT NOT NULL,
    downs INTEGER NOT NULL DEFAULT 0,
    ups INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_rule_proposals_status ON research_rule_proposals (status, created_at DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (80, datetime('now'));
