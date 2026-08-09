-- 0038_order_events.sql
-- REL-019 (R-022): append-only audit trail of order lifecycle events.
-- One row per successful submit / IB rejection / cancel / modify /
-- indeterminate outcome, keyed by orderRef/permId, so incident
-- reconstruction no longer depends on the order having filled.

CREATE TABLE IF NOT EXISTS order_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type  TEXT NOT NULL CHECK (event_type IN ('submitted', 'rejected', 'cancelled', 'modified', 'indeterminate')),
  order_ref   TEXT,
  order_id    INTEGER,
  perm_id     INTEGER,
  symbol      TEXT,
  action      TEXT,
  quantity    REAL,
  limit_price REAL,
  status      TEXT,
  detail      TEXT CHECK (detail IS NULL OR json_valid(detail)),
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_events_order_ref ON order_events (order_ref);
CREATE INDEX IF NOT EXISTS idx_order_events_created_at ON order_events (created_at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (38, datetime('now'));
