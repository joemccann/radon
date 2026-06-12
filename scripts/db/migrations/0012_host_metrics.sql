-- 0012_host_metrics.sql — DUR-12: minimal host/process metrics, solo-operator
-- sized (NO Prometheus stack — explicitly rejected for this box).
--
-- Both June wedges (the libsql event-loop freeze and the gateway API hang)
-- were resource-shaped and RCA needed ad-hoc py-spy because nothing recorded
-- CPU / memory / event-loop lag / restart counts on the 2 vCPU / 7.6 GB VPS.
-- One row per sampler run: scripts/host_metrics_sampler.py fires every
-- minute via radon-cloud radon-host-metrics.timer and writes over the
-- bounded hrana HTTP path (scripts/db/hrana_http.py) with a capped local
-- JSONL fallback.
--
-- Retention: the sampler prunes rows older than 14 days once an hour
-- (RETENTION_DAYS in host_metrics_sampler.py), following the
-- service_health_events prune precedent
-- (scripts/db/writer.py:prune_service_health_events).
--
-- WARNING — formatting is load-bearing: migrate.py:_split_statements and
-- migrate.ts:splitStatements split on /;\s*$/m (semicolon at end-of-line =
-- statement boundary). Keep every statement's terminating semicolon at
-- end-of-line and never put an interior semicolon at end-of-line. Pinned by
-- scripts/tests/test_migration_0012.py.

CREATE TABLE IF NOT EXISTS host_metrics (
  taken_at     TEXT NOT NULL,    -- UTC ISO-8601 timestamp of THIS sample
  cpu_pct      REAL,             -- whole-box CPU busy % over a 1s /proc/stat delta
  mem_used_mb  REAL,             -- MemTotal - MemAvailable, in MB
  mem_avail_mb REAL,             -- MemAvailable, in MB
  load1        REAL,             -- 1-minute load average (/proc/loadavg)
  swap_used_mb REAL,             -- SwapTotal - SwapFree, in MB (VPS runs 0 swap; nonzero = trouble)
  loop_lag_ms  REAL,             -- FastAPI event-loop lag from /health/lite (NULL when api unreachable)
  units_json   TEXT              -- JSON array of {unit, active_state, n_restarts} for radon-* units
);

CREATE INDEX IF NOT EXISTS idx_host_metrics_taken_at ON host_metrics(taken_at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (12, datetime('now'));
