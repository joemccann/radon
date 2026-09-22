-- 0085_redact_tv_alert_raw_body_nested_secret.sql
-- Hardening (CWE-312), follow-up to 0084: 0084's JSON branch only redacted a
-- TOP-LEVEL `$.secret` member via json_set. A JSON body carrying the secret
-- at any other path (e.g. nested under a wrapper key) has no top-level
-- `secret` for json_extract('$.secret') to find, so it survived 0084
-- untouched even though it still matches TradingView's `"secret":"…"` shape.
-- SQLite has no cheap recursive json-path rewrite, so this mirrors 0084's
-- non-JSON branch: a still-present `"secret"` (or `secret=`) marker anywhere
-- in a JSON body that 0084 did not already fully redact gets the same
-- whole-body replacement. Idempotent: an already-redacted row no longer
-- matches either LIKE clause.

UPDATE tv_alert_events
SET raw_body = '[REDACTED PRE-0085: body contained a nested secret token]'
WHERE json_valid(raw_body)
  AND json_extract(raw_body, '$.secret') IS NULL
  AND raw_body NOT LIKE '[REDACTED%'
  AND (raw_body LIKE '%"secret"%' OR raw_body LIKE '%secret=%');

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
  VALUES (85, datetime('now'));
