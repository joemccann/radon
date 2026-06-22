-- 0020_alert_rules.sql — F6 signal alert rules engine.
--
-- User-scoped alert rules keyed by the Clerk user_id (TEXT). Each rule is a
-- simple (metric op threshold) predicate evaluated against a fresh scan row for
-- a ticker; matches dispatch through the existing watchdog notification helper.
-- Conventions mirror 0010_user_profiles_bookmarks_watchlist.sql: ISO-8601 TEXT
-- timestamps (UTC), nullable optional columns.
--
-- NB: the F6 spec reserved migration 0017, but 0017 is already taken by the
-- F5 event-odds overlay (0017_event_odds.sql). This migration continues from
-- the next free slot above the coordinated F6/F12/F14 range to avoid clobbering
-- a sibling in-flight feature.

CREATE TABLE IF NOT EXISTS alert_rules (
  id            TEXT    PRIMARY KEY,
  user_id       TEXT    NOT NULL,
  ticker        TEXT    NOT NULL,
  metric        TEXT    NOT NULL,   -- e.g. flow_strength | score | buy_ratio
  op            TEXT    NOT NULL,   -- one of: > | < | >= | <=
  threshold     REAL    NOT NULL,
  channel       TEXT    NOT NULL,   -- pushover | service_health
  created_at    TEXT    NOT NULL,
  last_fired_at TEXT                -- nullable: throttle bookkeeping
);

CREATE INDEX IF NOT EXISTS alert_rules_user_date
  ON alert_rules(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS alert_rules_ticker
  ON alert_rules(ticker);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
  VALUES (20, datetime('now'));
