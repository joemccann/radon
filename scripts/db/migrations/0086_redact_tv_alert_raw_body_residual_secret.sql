-- 0086_redact_tv_alert_raw_body_residual_secret.sql
-- Hardening (CWE-312), follow-up to 0084/0085: 0085 only considered JSON
-- bodies with no top-level `$.secret`, so a body whose top-level secret 0084
-- had already replaced with [REDACTED] but which also carried the secret at
-- another path (or as a `secret=` token inside a string value) was skipped.
-- This pass inspects every JSON body that is not already wholesale-redacted:
-- any `secret` member anywhere whose value is not [REDACTED], or a residual
-- `secret=` token once the top-level member is set aside, gets the same
-- whole-body replacement 0085 used. Fully redacted bodies are left intact.
-- Idempotent: a replaced body is no longer valid JSON and no longer matches.

UPDATE tv_alert_events
SET raw_body = '[REDACTED PRE-0086: body contained a residual secret token]'
WHERE json_valid(raw_body)
  AND (
    EXISTS (
      SELECT 1 FROM json_tree(tv_alert_events.raw_body)
      WHERE json_tree.key = 'secret'
        AND (json_tree.atom IS NULL OR json_tree.atom <> '[REDACTED]')
    )
    OR json_remove(raw_body, '$.secret') LIKE '%secret=%'
  );

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
  VALUES (86, datetime('now'));
