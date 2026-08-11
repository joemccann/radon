-- 0046_app_preferences.sql
-- Operator-tunable runtime preferences backing the /preferences page.
-- One row per registry key declared in scripts/app_preferences.py. A row is an
-- OVERRIDE only: resolution is DB row > environment variable > code default,
-- and a value outside the registry's [hard_min, hard_max] band is IGNORED at
-- read time (falling through to env/default), so a bad row can never widen a
-- safety cap such as RADON_MAX_ORDER_NOTIONAL.
-- value holds the canonical text encoding produced by app_preferences:
-- integers as "500", floats as "250000.0", booleans as "true" / "false".
-- updated_by is the Clerk user id of the operator who wrote the row, derived
-- server-side from the authenticated principal, never from the request body.
-- Version 46 is the next free number: Turso is at 45 and the in-flight
-- equibles-integration worktree holds 0041 through 0045.

CREATE TABLE IF NOT EXISTS app_preferences (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_preferences_updated_at ON app_preferences (updated_at);

-- Append-only audit of every change to a live risk cap. app_preferences holds
-- one mutable row per key, so without this a cap could be raised, exploited and
-- reset with no trace. The event is written BEFORE the mutation and a failed
-- audit write refuses the change, so a value can never move unrecorded.
-- action is 'set' or 'reset'; new_value is NULL on a reset.
CREATE TABLE IF NOT EXISTS app_preference_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  key       TEXT NOT NULL,
  action    TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  actor     TEXT,
  at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_preference_events_at ON app_preference_events (at);
CREATE INDEX IF NOT EXISTS idx_app_preference_events_key ON app_preference_events (key, at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (46, datetime('now'));
