-- RSI OVERSOLD indicator: percent of current S&P 500 members whose own
-- 14-day Wilder RSI closed strictly below 30, one row per session,
-- computed from constituent closes in the shared price_history_daily
-- store (never a vendor series). spx_close is the ^GSPC session close
-- the chart overlays, NULL when the overlay fetch missed that session.
CREATE TABLE IF NOT EXISTS rsi_oversold_history (
    date TEXT PRIMARY KEY,
    pct_below_30 REAL NOT NULL,
    count_below_30 INTEGER NOT NULL,
    eligible INTEGER NOT NULL,
    spx_close REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rsi_oversold_history_date_desc ON rsi_oversold_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (92, datetime('now'));
