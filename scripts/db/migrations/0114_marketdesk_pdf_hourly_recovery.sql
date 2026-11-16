-- Post-quota recovery state. The hourly admission cap is enforced in the
-- same lease statement as the daily cap; it does not need a new table.
-- Two ALTERs plus one singleton UPDATE. No queue scan. This stays inside
-- the radon-api 20s boot migrate deadline (0112 was a long multi-ALTER file
-- that lost its Hrana stream; 0089 is the manual 14-minute drop).
-- Arm when the interval is still above the 900s ramp start, not only when
-- last_error is still a quota code. A later provider success clears
-- last_error and left the 2026-11-15 row at 2100s with last_error NULL.
-- A row still on the 900s ramp is not halved.
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN recover_armed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE marketdesk_pdf_throttle ADD COLUMN recover_credits INTEGER NOT NULL DEFAULT 0;
UPDATE marketdesk_pdf_throttle SET recover_armed=1, recover_credits=0
 WHERE singleton=1 AND (
  interval_seconds > 900
  OR last_error IN (
   'preview_quota','download_quota','http_429','http_401','http_402','http_403','unauthenticated'));
INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(114,datetime('now'));
