-- Adaptive PDF admission. The account interval and batch live on the
-- existing throttle row so two workers and a restart share one rate.
-- disposition takes policy rejects and exhausted items out of the backlog
-- without rewriting status, so a reader that only knows pending/downloading/
-- downloaded still parses the row.
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN interval_seconds INTEGER NOT NULL DEFAULT 300;
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN batch_size INTEGER NOT NULL DEFAULT 6;
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN soft_streak INTEGER NOT NULL DEFAULT 0;
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN success_credits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN jitter_bps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN lane INTEGER NOT NULL DEFAULT 0 CHECK(lane IN (0,1));
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN observed_pending INTEGER;
ALTER TABLE marketdesk_pdf_queue ADD COLUMN disposition TEXT NOT NULL DEFAULT 'active' CHECK(disposition IN ('active','filtered','exhausted'));
ALTER TABLE marketdesk_pdf_queue ADD COLUMN disposition_reason TEXT;
ALTER TABLE marketdesk_pdf_queue ADD COLUMN disposition_at TEXT;

-- One new lease advances the shared clock once. Later rows in the same
-- statement see the clock already in the future and do not move it again.
-- A lease that matches nothing never fires this trigger.
-- The interior semicolon stays on the END line. migrate.py splits on /;\s*$/m.
CREATE TRIGGER IF NOT EXISTS trg_marketdesk_pdf_admit
AFTER UPDATE OF lease_token ON marketdesk_pdf_queue
WHEN NEW.lease_token IS NOT NULL AND NEW.status='downloading'
 AND NEW.lease_token IS NOT OLD.lease_token
BEGIN
  UPDATE marketdesk_pdf_throttle
  SET next_attempt_at=strftime('%Y-%m-%dT%H:%M:%f+00:00', NEW.updated_at, '+' || (interval_seconds + (interval_seconds * jitter_bps / 10000)) || ' seconds'), lane=1-lane
  WHERE singleton=1 AND julianday(next_attempt_at)<=julianday(NEW.updated_at); END;

INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(112,datetime('now'));
