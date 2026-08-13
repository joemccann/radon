-- 0003_demo_webhook_events.sql — signed Clerk webhook replay ledger.

CREATE TABLE IF NOT EXISTS demo_webhook_events (
  event_id     TEXT PRIMARY KEY,
  event_type   TEXT NOT NULL,
  processed_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (3, datetime('now'));
