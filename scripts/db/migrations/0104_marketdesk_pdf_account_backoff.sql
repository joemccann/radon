-- Persist account-wide quota escalation across reports, deployments and restarts.
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN quota_failures INTEGER NOT NULL DEFAULT 0 CHECK(quota_failures BETWEEN 0 AND 3);
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN last_error TEXT;
ALTER TABLE marketdesk_pdf_queue ADD COLUMN download_token TEXT;
-- Cache only PDF eligibility, invalidated by current book/rules or article edits.
CREATE TABLE IF NOT EXISTS marketdesk_pdf_selection (
  article_id TEXT PRIMARY KEY,
  context_hash TEXT NOT NULL,
  post_updated_at TEXT,
  eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
  reason_code TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- Preserve the provider refusal already recorded by the preceding runtime.
UPDATE marketdesk_pdf_throttle SET last_error=(SELECT last_error FROM marketdesk_pdf_queue WHERE status='pending' AND last_error IS NOT NULL ORDER BY updated_at DESC LIMIT 1);
INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(104,datetime('now'));
