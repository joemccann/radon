-- Safety reset (2026-11-14). The AIMD controller stays. The live row ramps
-- from 900s and batch 1 instead of opening at 300s and batch 6. Attempts
-- already on the queue do not count toward exhaustion, and rows a5cdd5f1
-- marked exhausted are put back. MARKETDESK_PDF_DAILY_CAP (default 400, 0
-- off) is the rolling 24h admission cap, enforced by the lease.
ALTER TABLE marketdesk_pdf_queue ADD COLUMN attempts_baseline INTEGER NOT NULL DEFAULT 0;
UPDATE marketdesk_pdf_queue SET attempts_baseline=attempts;
UPDATE marketdesk_pdf_queue SET disposition='active', disposition_reason=NULL, disposition_at=NULL
 WHERE disposition='exhausted';
UPDATE marketdesk_pdf_throttle SET interval_seconds=900, batch_size=1 WHERE singleton=1;

CREATE TABLE IF NOT EXISTS marketdesk_pdf_admissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id TEXT NOT NULL,
  admitted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_marketdesk_pdf_admissions_at ON marketdesk_pdf_admissions(admitted_at);

DROP TRIGGER IF EXISTS trg_marketdesk_pdf_admit;
-- Interior semicolons stay on the END line. migrate.py splits on /;\s*$/m.
CREATE TRIGGER IF NOT EXISTS trg_marketdesk_pdf_admit
AFTER UPDATE OF lease_token ON marketdesk_pdf_queue
WHEN NEW.lease_token IS NOT NULL AND NEW.status='downloading'
 AND NEW.lease_token IS NOT OLD.lease_token
BEGIN
  UPDATE marketdesk_pdf_throttle SET next_attempt_at=strftime('%Y-%m-%dT%H:%M:%f+00:00', NEW.updated_at, '+' || (interval_seconds + (interval_seconds * jitter_bps / 10000)) || ' seconds'), lane=1-lane WHERE singleton=1 AND julianday(next_attempt_at)<=julianday(NEW.updated_at); INSERT INTO marketdesk_pdf_admissions(article_id, admitted_at) VALUES(NEW.article_id, NEW.updated_at); END;

INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(113,datetime('now'));
