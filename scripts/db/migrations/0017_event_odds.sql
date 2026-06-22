-- 0017_event_odds.sql — F5 Polymarket event-odds overlay.
--
-- One row per ticker holding the latest overlay snapshot: the set of mapped
-- Polymarket markets, their binary-midpoint implied probabilities, the
-- options-skew-implied probabilities, and the signed divergence. The JSON
-- payload mirrors the disk fallback at data/event_odds/{TICKER}.json.

CREATE TABLE IF NOT EXISTS event_odds (
  ticker    TEXT NOT NULL,
  scan_time TEXT NOT NULL,
  payload   TEXT NOT NULL,
  PRIMARY KEY (ticker)
);

CREATE INDEX IF NOT EXISTS idx_event_odds_scan_time
  ON event_odds(scan_time);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (17, datetime('now'));
