-- 0084_redact_tv_alert_raw_body.sql
-- Hardening (CWE-312): tv_alert_events.raw_body stored the TradingView alert
-- body verbatim, and the documented alert template puts the shared webhook
-- secret in that body. The route now redacts every occurrence of the
-- configured secret before the INSERT (web/lib/tvWebhook.ts redactSecret);
-- this migration scrubs rows written before the fix.
--
-- SQL cannot know the secret's value, so the scrub is shape-based and
-- deliberately conservative, mirroring 0005's approach:
--   - JSON bodies: overwrite only the `secret` member via json_set, keeping
--     the rest of the body intact for audit.
--   - non-JSON (text/plain) bodies that carry a secret token: no precise
--     in-place edit is possible without regex, so the whole body is replaced.
--     The parsed columns (symbol/price/interval/...) were already extracted
--     at insert time and no reader consumes raw_body (scripts/tv_alerts_drain.py
--     digests the parsed columns only), so nothing breaks.
-- Idempotent: already-redacted rows no longer match either WHERE clause.
--
-- Operator follow-up (outside this migration): rotate TV_WEBHOOK_SECRET,
-- using the comma-separated rotation-overlap support in TV_WEBHOOK_SECRET.

UPDATE tv_alert_events
SET raw_body = json_set(raw_body, '$.secret', '[REDACTED]')
WHERE json_valid(raw_body)
  AND json_extract(raw_body, '$.secret') IS NOT NULL
  AND json_extract(raw_body, '$.secret') <> '[REDACTED]';

UPDATE tv_alert_events
SET raw_body = '[REDACTED PRE-0084: body contained a secret token]'
WHERE NOT json_valid(raw_body)
  AND (raw_body LIKE '%"secret"%' OR raw_body LIKE '%secret=%');

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
  VALUES (84, datetime('now'));
