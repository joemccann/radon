-- VIX TS indicator: daily VIX / VIX3M term-structure ratio from the Cboe CDN.
-- ratio is stored because it is a pure per-row function of the two closes and
-- stays idempotent under re-upsert; no rolling or cumulative statistic is ever
-- stored (that would make rows order-dependent). spx_close is a nullable left
-- join used only for the chart overlay.
CREATE TABLE IF NOT EXISTS vixts_history (
    date TEXT PRIMARY KEY,
    vix_close REAL NOT NULL,
    vix3m_close REAL NOT NULL,
    ratio REAL NOT NULL,
    spx_close REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vixts_history_date_desc ON vixts_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (58, datetime('now'));
