-- 0011_service_health_events.sql — DUR-11: append-only service_health history
-- + deploy markers.
--
-- service_health (0001) is latest-state-only (PRIMARY KEY (service)), so
-- uptime %, MTTR, and flap counts were impossible. Triggers on service_health
-- mirror every state TRANSITION into service_health_events, covering all four
-- writer chokepoints (scripts/db/writer.py, scripts/db/writer.js,
-- web/lib/serviceHealth.ts, and the scripts/db/service_health_sql.py hrana
-- path) with zero app changes and no read round-trip per heartbeat: ok->ok
-- upsert-updates are suppressed by the WHEN clause.
--
-- service='deploy' rows are deploy markers (detail/last_error = deployed
-- commit SHA), written by radon-cloud deploy.sh after a green post-deploy
-- gate. Their state never changes (always 'ok'), so the UPDATE trigger needs
-- the OR NEW.service = 'deploy' arm or only the first deploy would ever land.
--
-- WARNING — formatting is load-bearing: migrate.py:_split_statements and
-- migrate.ts:splitStatements split on /;\s*$/m. Trigger bodies keep
-- "; END;" on ONE line so the interior semicolon is never at end-of-line.
-- Reformatting the trigger bodies breaks the migration at radon-api
-- ExecStartPre (= failed deploy). Pinned by scripts/tests/test_migration_0011.py.
--
-- Trigger support (AFTER INSERT, AFTER UPDATE + compound WHEN, upsert
-- semantics) verified against production Turso 2026-06-12 via a throwaway
-- _dur11_smoke table; nothing left behind.

CREATE TABLE IF NOT EXISTS service_health_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  service     TEXT NOT NULL,
  state       TEXT NOT NULL,    -- state entered: 'ok' | 'syncing' | 'error' | 'paused'
  detail      TEXT,             -- service_health.last_error snapshot (JSON), or deploy SHA
  created_at  TEXT NOT NULL     -- service_health.updated_at of the transition (UTC ISO)
);

CREATE INDEX IF NOT EXISTS idx_service_health_events_service_created
  ON service_health_events(service, created_at);

CREATE TRIGGER IF NOT EXISTS trg_service_health_event_on_insert
AFTER INSERT ON service_health
BEGIN
  INSERT INTO service_health_events (service, state, detail, created_at)
  VALUES (NEW.service, NEW.state, NEW.last_error, NEW.updated_at); END;

CREATE TRIGGER IF NOT EXISTS trg_service_health_event_on_update
AFTER UPDATE ON service_health
WHEN OLD.state != NEW.state OR NEW.service = 'deploy'
BEGIN
  INSERT INTO service_health_events (service, state, detail, created_at)
  VALUES (NEW.service, NEW.state, NEW.last_error, NEW.updated_at); END;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (11, datetime('now'));
