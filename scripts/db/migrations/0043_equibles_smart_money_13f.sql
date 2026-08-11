-- 0043_equibles_smart_money_13f.sql — 13F institutional positioning depth
-- (Equibles). Position-level holder rows plus the assembled per-ticker payload
-- that /api/equibles-smart-money-13f serves.
--
-- HONESTY CONTRACT: 13F is QUARTERLY and lands up to 45 days after quarter
-- end. It is CONTEXT, never a trigger — on its own it fails Gate 2 ("a signal
-- that hasn't moved price"). report_date is the filing vintage (quarter end,
-- YYYY-MM-DD) and is part of both primary keys, so re-running a quarter is
-- idempotent and a new quarter lands beside the old one instead of erasing it.
--
-- pct_of_institutional_total is a PERCENT in 0-100 and is the filer's share of
-- the REPORTED INSTITUTIONAL position, NOT of shares outstanding (Equibles
-- sends it as `percentOfTotal` against the 13F universe). Do not relabel it as
-- ownership of the float. All percents here are 0-100: EquiblesClient
-- normalizes the API's fraction fields to '...Pct' keys at its boundary, so
-- nothing downstream ever holds a fraction.

CREATE TABLE IF NOT EXISTS equibles_13f_holders (
  ticker                     TEXT NOT NULL,
  report_date                TEXT NOT NULL,  -- 13F vintage, quarter end YYYY-MM-DD
  cik                        TEXT NOT NULL,  -- filer CIK; '' when the source omits one
  institution                TEXT NOT NULL,
  shares                     REAL,
  value_usd                  REAL,
  pct_of_institutional_total REAL,           -- percent 0-100 of the 13F universe
  position_type              TEXT,           -- 'Common', 'Call', 'Put', ...
  change_in_shares           REAL,           -- joined from institutional-activity
  change_type                TEXT,           -- NEW | ADDED | TRIMMED | EXITED | HELD
  recorded_at                TEXT NOT NULL,
  PRIMARY KEY (ticker, report_date, cik)
);

-- The durable store's only read pattern: one ticker's newest vintage ranked by
-- position value (top-holders-by-value, and the QoQ diff of two vintages).
CREATE INDEX IF NOT EXISTS idx_equibles_13f_holders_by_value
  ON equibles_13f_holders(ticker, report_date DESC, value_usd DESC);

CREATE TABLE IF NOT EXISTS equibles_13f_snapshots (
  ticker      TEXT NOT NULL,
  report_date TEXT NOT NULL,
  scan_time   TEXT NOT NULL,
  payload     TEXT NOT NULL,  -- disclosure vintage + headline + holders + series
  PRIMARY KEY (ticker, report_date)
);

-- The route reads `WHERE ticker = ? ORDER BY report_date DESC LIMIT 1`; the
-- composite primary key above is that covering index, so no second one here.

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (43, datetime('now'));
