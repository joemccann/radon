-- Post-quota recovery state. The hourly admission cap is enforced in the
-- same lease statement as the daily cap; it does not need a new table.
-- Arm recovery only when the live row is still sitting on a quota or
-- suspension signal, so an in-progress 900s ramp is not halved.
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN recover_armed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN recover_credits INTEGER NOT NULL DEFAULT 0;
UPDATE marketdesk_pdf_throttle SET recover_armed=1, recover_credits=0
 WHERE singleton=1 AND last_error IN (
  'preview_quota','download_quota','http_429','http_401','http_402','http_403','unauthenticated');
INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(114,datetime('now'));
