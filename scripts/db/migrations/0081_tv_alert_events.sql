-- TradingView alert webhook ledger (docs/tradingview-integration.md, Phase 1).
-- Append-only. The Next route inserts the raw body before parsing, then fills
-- the parsed columns; scripts/tv_alerts_drain.py resolves `ticker`, marks
-- 5-second exact repeats in `duplicate_of`, sends one digest Pushover per
-- cycle, and prunes rows older than 180 days.
CREATE TABLE IF NOT EXISTS tv_alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    source_ip TEXT,
    raw_body TEXT NOT NULL,
    symbol TEXT, exchange TEXT, ticker TEXT,
    price REAL, interval TEXT, alert_name TEXT,
    bar_time TEXT, sent_at TEXT,
    parse_error TEXT,
    duplicate_of INTEGER,
    processed_at TEXT,
    digest_sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tv_alert_events_unprocessed ON tv_alert_events (processed_at, id);
CREATE INDEX IF NOT EXISTS idx_tv_alert_events_received_desc ON tv_alert_events (received_at DESC);
