-- TradingView source variant, not verified StockCharts parity.
-- Fixed 500 normalization is explicit; NULL values are missing observations.
CREATE TABLE IF NOT EXISTS record_high_breadth_history (
    date TEXT PRIMARY KEY,
    spx_close REAL CHECK (spx_close >= 0),
    above_200_pct REAL CHECK (above_200_pct >= 0 AND above_200_pct <= 100),
    new_highs INTEGER CHECK (new_highs >= 0),
    new_lows INTEGER CHECK (new_lows >= 0),
    net_high_low_per_500 REAL,
    source_timestamps TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (94, datetime('now'));
