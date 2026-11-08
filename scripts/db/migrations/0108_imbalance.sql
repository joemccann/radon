-- TradingView USI ASK minus BID trade counts. NULL is a missing observation,
-- never a zero print. BATD is stored for parity only.
CREATE TABLE IF NOT EXISTS imbalance_history (
    date TEXT PRIMARY KEY,
    ask INTEGER CHECK (ask >= 0),
    bid INTEGER CHECK (bid >= 0),
    batd INTEGER,
    imbalance INTEGER,
    ema10 REAL,
    spx_close REAL CHECK (spx_close > 0),
    source_timestamps TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (108, datetime('now'));
