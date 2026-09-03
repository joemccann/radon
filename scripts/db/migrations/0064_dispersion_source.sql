-- 0064_dispersion_source.sql — R-447: which rung built each DISPERSION row.
-- The payload's source label was replaced by "stored" on the next
-- no-new-session refresh, so a Yahoo-built or mixed night lost its
-- provenance after one day. NULL = written before this column existed.
--
-- WARNING — formatting is load-bearing: migrate.py:_split_statements and
-- migrate.ts:splitStatements split on /;\s*$/m (semicolon at end-of-line =
-- statement boundary). Keep every statement's terminating semicolon at
-- end-of-line and never put an interior semicolon at end-of-line.

ALTER TABLE dispersion_history ADD COLUMN source TEXT;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (64, datetime('now'));
