-- 0003_demo_webhook_events.sql — signed Clerk webhook replay ledger.

CREATE TABLE IF NOT EXISTS demo_webhook_events (
  event_id     TEXT PRIMARY KEY,
  event_type   TEXT NOT NULL,
  processed_at TEXT NOT NULL
);
