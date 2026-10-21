CREATE TABLE IF NOT EXISTS high_low_line_history (
    interval TEXT NOT NULL CHECK(interval IN ('daily', 'weekly')),
    date TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    period_complete INTEGER NOT NULL CHECK(period_complete IN (0, 1)),
    highs REAL NOT NULL CHECK(highs >= 0),
    lows REAL NOT NULL CHECK(lows >= 0),
    net REAL NOT NULL,
    cumulative REAL NOT NULL,
    rsi14 REAL,
    stoch_rsi14 REAL,
    sar REAL,
    sar_direction TEXT CHECK(sar_direction IN ('rising', 'falling')),
    reset_to_zero INTEGER NOT NULL CHECK(reset_to_zero IN (0, 1)),
    source_fractional INTEGER NOT NULL CHECK(source_fractional IN (0, 1)),
    recorded_at TEXT NOT NULL,
    UNIQUE(interval, period_start),
    PRIMARY KEY(interval, date)
);
CREATE INDEX IF NOT EXISTS idx_high_low_line_history_date
    ON high_low_line_history(interval, date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at)
    VALUES (101, datetime('now'));
