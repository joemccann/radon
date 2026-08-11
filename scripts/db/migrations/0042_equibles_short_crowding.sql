-- 0042_equibles_short_crowding.sql — Short crowding / squeeze scoreboard
-- (Equibles: FINRA bi-monthly short interest + the 0-100 squeeze score).
--
-- equibles_short_interest is the durable per-settlement series, one row per
-- (ticker, settlement_date). settlement_date is the FINRA settlement the
-- figures belong to, NOT the day they were fetched — short interest reports
-- with a lag, so every read surface renders this vintage rather than implying
-- the numbers are live.
--
-- equibles_squeeze_scores holds the latest score per ticker (score, its base
-- and catalyst components, and the six factor percentiles as a JSON blob so
-- new factors don't need a migration). Ratio fields arrive from
-- EquiblesClient already normalized to percent (0-100), never fractions.

CREATE TABLE IF NOT EXISTS equibles_short_interest (
  ticker                   TEXT NOT NULL,
  settlement_date          TEXT NOT NULL,  -- YYYY-MM-DD
  short_position           REAL,           -- shares held short
  change_in_short_position REAL,           -- shares vs prior settlement
  average_daily_volume     REAL,
  days_to_cover            REAL,
  recorded_at              TEXT NOT NULL,
  PRIMARY KEY (ticker, settlement_date)
);

CREATE INDEX IF NOT EXISTS idx_equibles_short_interest_ticker_date
  ON equibles_short_interest (ticker, settlement_date DESC);

CREATE INDEX IF NOT EXISTS idx_equibles_short_interest_date
  ON equibles_short_interest (settlement_date DESC);

CREATE TABLE IF NOT EXISTS equibles_squeeze_scores (
  ticker                     TEXT PRIMARY KEY,
  score                      REAL,          -- 0-100
  rank                       INTEGER,
  base_score                 REAL,
  catalyst_boost             REAL,
  short_interest_pct         REAL,          -- percent 0-100, not a fraction
  short_interest_pct_basis   TEXT,          -- 'shares' | 'float' (not interchangeable)
  market_cap                 REAL,
  factors                    TEXT,          -- JSON: factor percentile breakdown
  as_of                      TEXT,          -- settlement vintage the score reflects
  recorded_at                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_equibles_squeeze_scores_score
  ON equibles_squeeze_scores (score DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (42, datetime('now'));
