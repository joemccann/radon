-- 0050_host_metrics_disk_pct.sql — R-069: root-filesystem usage joins the
-- minute-cadence host metrics row. The sampler covered CPU / memory / swap /
-- load but nothing on the box recorded disk, so an unbounded writer (the
-- data/uw_http_cache/ growth this REL-038 item also fixes) could fill the
-- VPS root fs with no trail for RCA. NULL = usage unreadable, mirroring
-- loop_lag_ms.
--
-- WARNING — formatting is load-bearing: migrate.py:_split_statements and
-- migrate.ts:splitStatements split on /;\s*$/m (semicolon at end-of-line =
-- statement boundary). Keep every statement's terminating semicolon at
-- end-of-line and never put an interior semicolon at end-of-line.

ALTER TABLE host_metrics ADD COLUMN disk_pct REAL;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (50, datetime('now'));
