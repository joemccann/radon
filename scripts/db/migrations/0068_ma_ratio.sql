-- MA RATIO indicator: percent of S&P 500 members above their 50-day SMA
-- over percent above their 200-day SMA, one row per session, computed from
-- constituent closes in the shared price_history_daily store (never a
-- vendor ratio series). ratio is NULL when pct_above_200 is zero (the
-- documented zero-denominator guard); spx_close is the ^GSPC session close
-- the chart overlays, NULL when the overlay fetch missed that session.
CREATE TABLE IF NOT EXISTS ma_ratio_history (
    date TEXT PRIMARY KEY,
    pct_above_50 REAL NOT NULL,
    pct_above_200 REAL NOT NULL,
    ratio REAL,
    spx_close REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ma_ratio_history_date_desc ON ma_ratio_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (68, datetime('now'));
