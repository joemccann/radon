-- Operator-requested cohorts outrank ordinary work without changing retry clocks.
ALTER TABLE marketdesk_pdf_queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1));
CREATE INDEX IF NOT EXISTS idx_marketdesk_pdf_priority ON marketdesk_pdf_queue(priority,status,next_attempt_at);
INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(105,datetime('now'));
