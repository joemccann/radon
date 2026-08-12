-- 0041_vol_cone.sql — VOL CONE: per-session ATM + 10% OTM wing IVs for one
-- fixed monthly expiry (third Friday, DTE in [21, 90] closest to 45).
-- IV is stored as a decimal (0.385 = 38.5 vol points). One row per
-- (ticker, date, expiry); first-run backfill is ~80 sessions per name.

CREATE TABLE IF NOT EXISTS vol_cone_history (
  ticker         TEXT NOT NULL,
  date           TEXT NOT NULL,
  expiry         TEXT NOT NULL,
  dte            INTEGER NOT NULL,
  spot           REAL NOT NULL,
  atm_iv         REAL NOT NULL,
  call_10_iv     REAL NOT NULL,
  put_10_iv      REAL NOT NULL,
  call_10_strike REAL,
  put_10_strike  REAL,
  recorded_at    TEXT NOT NULL,
  PRIMARY KEY (ticker, date, expiry)
);
CREATE INDEX IF NOT EXISTS idx_vol_cone_history_ticker_date
  ON vol_cone_history (ticker, date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (41, datetime('now'));
