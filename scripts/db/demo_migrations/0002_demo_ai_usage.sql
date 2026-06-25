-- 0002_demo_ai_usage.sql — per-trial AI call counter (DEMO Turso DB ONLY).
--
-- ⛔ DEMO DB ONLY. See 0001_demo_users.sql for the isolation contract.
--
-- One row per (user, endpoint, ET calendar day). The Next.js LLM routes
-- (assistant 5/day, ticker/seasonality 10/day, ticker/info 20/day) call
-- web/lib/demo/aiQuota.ts:checkAndIncrementQuota, which atomically inserts
-- the day's row (call_count=1) or increments it. Reset is implicit: a new
-- ET day produces a new day_et key. No TTL / sweep required — old rows are
-- harmless and can be pruned by an operator job if ever needed.

CREATE TABLE IF NOT EXISTS demo_ai_usage (
  user_id     TEXT    NOT NULL,
  endpoint    TEXT    NOT NULL,           -- e.g. 'assistant', 'ticker/seasonality', 'ticker/info'
  day_et      TEXT    NOT NULL,           -- YYYY-MM-DD in America/New_York
  call_count  INTEGER NOT NULL,
  PRIMARY KEY (user_id, endpoint, day_et)
);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (2, datetime('now'));
