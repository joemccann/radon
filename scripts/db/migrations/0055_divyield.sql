-- DIVYIELD indicator: percent of S&P 500 stocks with trailing dividend
-- yield above the 10-Year Treasury yield. One row per date: daily rows
-- from the timer (approximate = 0) plus monthly 1990+ backfill rows
-- (approximate = 1, survivorship-biased approximation).
CREATE TABLE IF NOT EXISTS divyield_history (
    date TEXT PRIMARY KEY,
    pct_above REAL NOT NULL,
    count_above INTEGER NOT NULL,
    total INTEGER NOT NULL,
    y10 REAL NOT NULL,
    approximate INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_divyield_history_date_desc ON divyield_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (55, datetime('now'));
