-- 0049_vixcor.sql — VIXCOR indicator: the rolling 20-session Pearson
-- correlation between the VIX close and the Cboe 3-month SPX implied
-- correlation close, over the inner join of the two session calendars
-- (== the cor3m calendar; cor3m dates are a strict subset of VIX dates).
--
-- corr20 is NULL for the first WINDOW-1 joined sessions and for any
-- degenerate window where one leg is constant. vix_close and cor3m_close
-- are denormalized onto the row so the chart renders both panes from one
-- table without a second join at read time. Breakdown episodes are NOT
-- stored: they are a pure function of corr20 plus four named constants and
-- are recomputed in the ingest job on every run (see docs/indicators/vixcor.md
-- section C.2).

CREATE TABLE IF NOT EXISTS vixcor_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD, a joined session date
  vix_close   REAL NOT NULL,      -- Cboe VIX_History.csv CLOSE
  cor3m_close REAL NOT NULL,      -- cor_history.cor3m for the same date
  corr20      REAL,               -- NULL for the first 19 rows / degenerate windows
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vixcor_history_date ON vixcor_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (49, datetime('now'));
