-- 0111_msr.sql — MSR indicator: move surprise ratio for SPX, NDX and RUT,
-- one row per (index, session) from 2007-11-08 (the Goldman chart start;
-- NDX/RUT begin 2009-09-15/17, bounded by the VXN/RVX history floors).
--
-- msr = |1-day % move| / prior-close Cboe vol index (VIX / VXN / RVX).

CREATE TABLE IF NOT EXISTS msr_history (
  index_symbol TEXT NOT NULL,      -- 'SPX' | 'NDX' | 'RUT'
  date         TEXT NOT NULL,      -- YYYY-MM-DD session t
  close        REAL NOT NULL,
  iv_prior     REAL NOT NULL,      -- vol index close at t-1
  move_pct     REAL NOT NULL,
  msr          REAL NOT NULL,
  recorded_at  TEXT NOT NULL,
  PRIMARY KEY (index_symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_msr_history_date ON msr_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (111, datetime('now'));
