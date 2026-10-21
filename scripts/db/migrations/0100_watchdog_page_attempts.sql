-- 0100_watchdog_page_attempts.sql — durable budget ledger for the grok
-- page responder's daily action cap.
--
-- GROK_PAGE_MAX_ACTIONS_PER_DAY bounds how many times the responder may
-- launch its always-approve agent per UTC day. That read used to count
-- watchdog_pages rows by claimed_at, but a failed attempt clears
-- claimed_at on purpose so the ticket can be retried — so retry
-- semantics and budget accounting shared one column and every failed or
-- fallback launch dropped out of the count.
--
-- One row is reserved BEFORE each launch and never cleared, so the cap
-- counts attempts independently of ticket claim state.

CREATE TABLE IF NOT EXISTS watchdog_page_attempts (
  attempt_id   TEXT PRIMARY KEY,
  page_id      TEXT NOT NULL,
  kind         TEXT NOT NULL,          -- grok | oneshot_rerun
  attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchdog_page_attempts_at
  ON watchdog_page_attempts (attempted_at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
  VALUES (100, datetime('now'));
