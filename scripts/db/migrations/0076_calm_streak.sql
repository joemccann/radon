-- CALM STREAK indicator: consecutive SPX sessions without a >1% intraday band
-- (band_pct = 100 * (high - low) / prior close; exactly 1.0 does not break).
-- One row per completed session from 1985, Cboe official _SPX daily OHLC.
-- open is NULL where Cboe reports 0 (pre-1996 seam); unused by the definition.
CREATE TABLE IF NOT EXISTS calm_streak_history (
    date TEXT PRIMARY KEY,
    open REAL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    band_pct REAL NOT NULL,
    streak INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calm_streak_history_date_desc ON calm_streak_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (76, datetime('now'));
