-- DISPERSION indicator: one raw row per completed session. Only pure per-session
-- functions of the day's closes are stored (VIX close, 95-5 spread across the
-- S&P 500 seed, 95-5 spread across the 11 sector SPDRs, and the cross-section
-- sizes). The 60-session means and the since-2017 z-scores are rebuilt from
-- these rows every run because the z-score base is the whole sample; storing
-- them would make rows order-dependent.
CREATE TABLE IF NOT EXISTS dispersion_history (
    date TEXT PRIMARY KEY,
    vix_close REAL NOT NULL,
    stock_spread REAL NOT NULL,
    sector_spread REAL NOT NULL,
    n_stocks INTEGER NOT NULL,
    n_sectors INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dispersion_history_date_desc ON dispersion_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (61, datetime('now'));
