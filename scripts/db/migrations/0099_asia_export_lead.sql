CREATE TABLE IF NOT EXISTS asia_export_lead_history (
    date TEXT PRIMARY KEY,
    lead_date TEXT NOT NULL,
    taiwan_exports_usd_mn REAL,
    korea_exports_usd_mn REAL,
    taiwan_yoy_pct REAL,
    korea_yoy_pct REAL,
    mean_yoy_pct REAL,
    ma3_yoy_pct REAL,
    ma3_change_pp REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asia_export_lead_history_date
    ON asia_export_lead_history(date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at)
    VALUES (99, datetime('now'));
