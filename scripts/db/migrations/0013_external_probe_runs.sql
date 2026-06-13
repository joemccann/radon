-- 0013_external_probe_runs.sql — DUR-16: append-only Tier-3 probe HISTORY.
--
-- external_probe (0008) is latest-per-source only (PRIMARY KEY (source)), so
-- the off-box prober could never answer "how long was the edge down" or "did
-- the user path flap overnight". One row per probe run, written by
-- scripts/health_probe/probe.py over the stdlib-only Turso HTTP path
-- (scripts/health_probe/turso_http.py) alongside the existing single-row
-- upsert. New per-run signals: user_path_ok (unauthenticated /dashboard must
-- hit the Clerk wall — catches the Edge-runtime crash class) and
-- freshness_ok/tick_fresh/scan_fresh (the authenticated /api/probe/freshness
-- aggregate; NULL while the endpoint is pending deploy or the market is
-- quiet — NULL means "unknown", never "failed").
--
-- Retention: the prober prunes rows older than 30 days on every run
-- (turso_http.build_insert_run_pipeline), following the host_metrics /
-- service_health_events prune precedent.
--
-- WARNING — formatting is load-bearing: migrate.py:_split_statements and
-- migrate.ts:splitStatements split on /;\s*$/m (semicolon at end-of-line =
-- statement boundary). Keep every statement's terminating semicolon at
-- end-of-line and never put an interior semicolon at end-of-line. Pinned by
-- scripts/tests/test_migration_0013.py.

CREATE TABLE IF NOT EXISTS external_probe_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at       TEXT NOT NULL,    -- UTC ISO-8601 timestamp of THIS probe run
  edge_ok      INTEGER NOT NULL, -- 1 = /edge-health/{ping,status} reachable + healthy
  user_path_ok INTEGER NOT NULL, -- 1 = unauthenticated /dashboard hit the Clerk wall
  freshness_ok INTEGER,          -- 1/0 per /api/probe/freshness; NULL = pending/unknown
  tick_fresh   INTEGER,          -- checks.relay_tick.fresh (NULL when n/a)
  scan_fresh   INTEGER,          -- vcg_scan AND gex_scan fresh, null-safe (NULL when n/a)
  detail       TEXT,             -- JSON {edge, user_path, freshness, market_state}
  latency_ms   REAL              -- worst-case round-trip across the probed endpoints
);

CREATE INDEX IF NOT EXISTS idx_external_probe_runs_run_at ON external_probe_runs(run_at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (13, datetime('now'));
