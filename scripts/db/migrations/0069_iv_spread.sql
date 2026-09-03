-- 0069_iv_spread.sql — IV SPREAD indicator: NDX minus SPX 30-day ATM implied
-- volatility in vol points. spx_iv / ndx_iv are the raw annualized decimal
-- closes from IB OPTION_IMPLIED_VOLATILITY daily bars. spread is NULL only
-- for a session the leg-level bad-print gate excluded (the raw legs stay).

CREATE TABLE IF NOT EXISTS iv_spread_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD session date
  spx_iv      REAL NOT NULL,      -- SPX 30d ATM IV close, annualized decimal
  ndx_iv      REAL NOT NULL,      -- NDX 30d ATM IV close, annualized decimal
  spread      REAL,               -- (ndx_iv - spx_iv) * 100, NULL when excluded
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_iv_spread_history_date ON iv_spread_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (69, datetime('now'));
