-- PANIC INDEX proxy: equal-weight mean of 252-session z-scores of VIX, VVIX,
-- VIX/VIX3M and Cboe SKEW, plus its one-day change. Unlike vixts_history the
-- derived columns ARE rolling statistics; that is safe here because the
-- fetcher rebuilds and re-upserts the ENTIRE series from full-history Cboe
-- files on every changed run, so no row is ever computed from a partial
-- history. Never write this table incrementally.
CREATE TABLE IF NOT EXISTS panic_index_history (
    date TEXT PRIMARY KEY,
    vix_close REAL NOT NULL,
    vix3m_close REAL NOT NULL,
    vvix_close REAL NOT NULL,
    skew_close REAL NOT NULL,
    ts_ratio REAL NOT NULL,
    z_vix REAL,
    z_vvix REAL,
    z_ts REAL,
    z_skew REAL,
    level REAL,
    delta_1d REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_panic_index_history_date_desc ON panic_index_history (date DESC);
CREATE INDEX IF NOT EXISTS idx_panic_index_history_delta ON panic_index_history (delta_1d);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (77, datetime('now'));
