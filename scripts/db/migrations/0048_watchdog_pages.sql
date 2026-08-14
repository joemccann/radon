-- 0048_watchdog_pages.sql — tickets for laptop Grok auto-response.
--
-- Written by the Hetzner watchdog the moment a P1 Pushover is accepted
-- (the same send that buzzes the operator iPhone). The laptop launchd
-- job `com.radon.grok-page-responder` claims a pending row and runs
-- headless Grok to diagnose and, when safe, ship a fix.
--
-- page_id is a stable hash of (service, severity, kind, UTC hour) so a
-- flapping writer cannot enqueue more than one ticket per cooldown window.

CREATE TABLE IF NOT EXISTS watchdog_pages (
  page_id         TEXT    PRIMARY KEY,
  service         TEXT    NOT NULL,
  severity        TEXT    NOT NULL,
  kind            TEXT    NOT NULL,
  message_excerpt TEXT    NOT NULL,
  paged_at        TEXT    NOT NULL,
  status          TEXT    NOT NULL,          -- pending | claimed | done | skipped
  claimed_at      TEXT,
  claim_token     TEXT,
  finished_at     TEXT,
  result          TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_watchdog_pages_status_paged
  ON watchdog_pages (status, paged_at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
  VALUES (48, datetime('now'));
