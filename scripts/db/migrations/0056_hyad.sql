-- HYAD indicator: FINRA TRACE high-yield corporate bond breadth, one row
-- per trading day (CORP + CORP_144A fieldC counts summed per date).
-- net/cum/ma21/ma50 are derived at payload build, never stored (storing a
-- cumulative would make rows order-dependent and break idempotent revisions).
CREATE TABLE IF NOT EXISTS hyad_history (
    date TEXT PRIMARY KEY,
    advances INTEGER NOT NULL,
    declines INTEGER NOT NULL,
    unchanged INTEGER NOT NULL,
    total INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hyad_history_date_desc ON hyad_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (56, datetime('now'));
