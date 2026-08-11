-- 0044_equibles_cot_positioning.sql — CFTC Commitments of Traders cross-asset
-- positioning (Equibles /v1/cftc), the first non-equity/vol series on /regime.
--
-- One row per (contract, weekly report). report_date is the TUESDAY the report
-- measures; the CFTC publishes it the following Friday afternoon ET, so every
-- row is structurally ~3 days old when it lands — freshness copy must derive
-- from report_date, never from a hardcoded cadence string.
--
-- Positions are contract counts as published. net_* are long minus short
-- (published nets win when the API sends them); net_noncommercial_pct_oi
-- expresses the speculative net as a percent of open interest so contracts of
-- wildly different sizes are comparable on one axis.

CREATE TABLE IF NOT EXISTS cot_positioning (
  market_code              TEXT NOT NULL,  -- CFTC market code
  report_date              TEXT NOT NULL,  -- yyyy-MM-dd (the measured Tuesday)
  contract_name            TEXT,
  category                 TEXT,           -- EquityIndices | Metals | Energy | ...
  open_interest            REAL,
  commercial_long          REAL,
  commercial_short         REAL,
  noncommercial_long       REAL,
  noncommercial_short      REAL,
  noncommercial_spread     REAL,
  net_commercial           REAL,
  net_noncommercial        REAL,
  net_noncommercial_pct_oi REAL,
  recorded_at              TEXT NOT NULL,
  PRIMARY KEY (market_code, report_date)
);

-- Per-contract history read (the chart series) and the cross-asset latest-week
-- board read.
CREATE INDEX IF NOT EXISTS idx_cot_positioning_contract_date
  ON cot_positioning(market_code, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_cot_positioning_date
  ON cot_positioning(report_date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (44, datetime('now'));
