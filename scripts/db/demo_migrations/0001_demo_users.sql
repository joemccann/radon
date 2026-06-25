-- 0001_demo_users.sql — demo.radon.run trial registry (DEMO Turso DB ONLY).
--
-- ⛔ This migration directory applies ONLY to the separate demo Turso DB
-- (TURSO_DEMO_DB_URL). It must NEVER run against the production DB. The
-- demo seeder (scripts/db/demo_seed.py) and the CI isolation guard
-- (scripts/ci/check_demo_isolation.py) both hard-refuse a prod-looking URL.
--
-- One row per self-serve trial user. Written at signup on the Clerk
-- `user.created` webhook (Phase 2) and mirrored from Clerk publicMetadata.
-- `expires_at` is 16:00 ET of the 3rd trading day from signup, computed by
-- scripts/utils/demo_trial.py:compute_trial_expiry (weekends + US holidays +
-- IBKR closures excluded via the existing market calendar).

CREATE TABLE IF NOT EXISTS demo_users (
  user_id     TEXT    PRIMARY KEY,        -- Clerk user id
  email       TEXT,
  demo_role   TEXT,                       -- Clerk publicMetadata.demoRole, e.g. 'trial'
  started_at  TEXT,                       -- ISO-8601 ET, trial clock start
  expires_at  TEXT,                       -- ISO-8601 ET, 16:00 ET of 3rd trading day
  status      TEXT    NOT NULL,           -- 'active' | 'expired' | 'revoked'
  revoked_at  TEXT,                       -- ISO-8601 ET, set on operator REVOKE
  created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_demo_users_status     ON demo_users(status);
CREATE INDEX IF NOT EXISTS idx_demo_users_expires_at ON demo_users(expires_at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, datetime('now'));
