-- HHLEV indicator: US household leverage (Z.1 B.101 family), one row per
-- quarter keyed by FRED's quarter-start observation_date. leverage_pct is
-- stored alongside its TLBSHNO/TNWBSHNO components ($ millions); Z.1
-- revises full history each release, so every run re-upserts all rows.
CREATE TABLE IF NOT EXISTS hhlev_history (
    date TEXT PRIMARY KEY,
    leverage_pct REAL NOT NULL,
    liabilities_musd REAL NOT NULL,
    net_worth_musd REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hhlev_history_date_desc ON hhlev_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (57, datetime('now'));
