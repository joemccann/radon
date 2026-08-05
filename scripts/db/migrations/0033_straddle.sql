-- 0033_straddle.sql — STRADDLE indicator: SPX realized vs implied 1-day
-- straddle ratio, one row per common SPX/VIX1D session (2022-05-13+, the
-- VIX1D history floor).
--
-- ratio = signed SPX close-to-close move divided by the PRIOR close's
-- implied 1-day straddle (Brenner-Subrahmanyam 0.8 x VIX1D x sqrt(1/252)).
-- NULL on the first session of the series (no prior close to price the
-- straddle from).

CREATE TABLE IF NOT EXISTS straddle_history (
  date        TEXT PRIMARY KEY,
  spx_close   REAL NOT NULL,
  vix1d_close REAL NOT NULL,
  ratio       REAL,               -- NULL on the first session (no prior close)
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_straddle_history_date ON straddle_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (33, datetime('now'));
