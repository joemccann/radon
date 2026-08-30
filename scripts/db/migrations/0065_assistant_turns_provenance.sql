-- 0065_assistant_turns_provenance.sql — R-454 / R-457: which turns shipped an
-- image, and to which vendor. Pasted screenshots of the operator UI carry real
-- account figures and are forwarded to the selected third-party model
-- provider; the turn row recorded only text, so a leak review could not say
-- which turns sent images where. `provider` / `model` are the ones that
-- ANSWERED (a fallback provider, not the one the picker requested).
--
-- WARNING — formatting is load-bearing: migrate.py:_split_statements and
-- migrate.ts:splitStatements split on /;\s*$/m (semicolon at end-of-line =
-- statement boundary). Keep every statement's terminating semicolon at
-- end-of-line and never put an interior semicolon at end-of-line. Both
-- runners treat `duplicate column name` on replay as applied (R-153).

ALTER TABLE assistant_turns ADD COLUMN image_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE assistant_turns ADD COLUMN provider TEXT;

ALTER TABLE assistant_turns ADD COLUMN model TEXT;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (63, datetime('now'));
