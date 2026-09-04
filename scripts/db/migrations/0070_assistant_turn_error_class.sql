-- 0070_assistant_turn_error_class.sql — R-624: an assistant turn that failed
-- recorded `outcome = 'error'` and nothing else. The only surviving record of
-- WHY was console.error into journald, so the class of failure (a provider
-- 429, an upstream 5xx, a tool crash, a timeout) was queryable nowhere and
-- turn 1 and turn 200 of a sustained outage were indistinguishable — a
-- suppression with no counter and no dwell bound.
--
-- WARNING — formatting is load-bearing: migrate.py:_split_statements and
-- migrate.ts:splitStatements split on /;\s*$/m (semicolon at end-of-line =
-- statement boundary). Keep every statement's terminating semicolon at
-- end-of-line and never put an interior semicolon at end-of-line. Both
-- runners treat `duplicate column name` on replay as applied (R-153).

ALTER TABLE assistant_turns ADD COLUMN error_class TEXT;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (70, datetime('now'));
