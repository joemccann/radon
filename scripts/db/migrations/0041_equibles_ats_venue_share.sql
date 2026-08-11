-- 0041_equibles_ats_venue_share.sql — ATS venue-share regime (Equibles/FINRA).
--
-- One row per (ticker, FINRA week). Raw weekly off-exchange volumes as
-- published, plus the metrics derived at ingestion time:
--
--   ats_share_pct           ats_volume / total_off_exchange_volume, 0-100.
--                           Dark-pool share of off-exchange flow, and the
--                           venue-mix metric the classification uses.
--   avg_ats_print_size      ats_volume / ats_trade_count (block footprint)
--   avg_non_ats_print_size  non_ats_otc_volume / non_ats_otc_trade_count
--   *_z                     trailing 52-week z-score per ticker, NULL until
--                           the minimum observation count is met
--   short_volume_pct        weekly FINRA short-volume share, 0-100, computed
--                           from the daily volumes (see the fetcher docstring
--                           on the upstream percent field)
--
-- off_exchange_share_pct is off-exchange volume over CONSOLIDATED (all-venue)
-- volume. Equibles publishes no consolidated denominator today, so the column
-- is NULL and consolidated_volume_source records where a denominator came
-- from if one ever appears. The daily short-volume file's totalVolume is NOT
-- that denominator: it is itself off-exchange only (verified live 2026-08-11,
-- AAPL ~14.9M/day against a ~50M/day consolidated tape).
--
-- classification is the joint reading: accumulation requires BOTH the ATS
-- share and the average ATS print size to be elevated in the same week.
-- Either alone is noise. classification_reason carries the guard text when
-- thin or flat history forces a neutral read.

CREATE TABLE IF NOT EXISTS equibles_ats_venue_share (
  ticker                    TEXT NOT NULL,
  week_start_date           TEXT NOT NULL,  -- YYYY-MM-DD (Monday of the FINRA week)
  ats_volume                REAL,
  ats_trade_count           REAL,
  non_ats_otc_volume        REAL,
  non_ats_otc_trade_count   REAL,
  total_off_exchange_volume REAL,
  ats_share_pct             REAL,
  avg_ats_print_size        REAL,
  avg_non_ats_print_size    REAL,
  ats_share_z               REAL,
  avg_ats_print_size_z      REAL,
  short_volume_pct          REAL,
  consolidated_volume       REAL,
  consolidated_volume_source TEXT,
  off_exchange_share_pct    REAL,
  classification            TEXT NOT NULL,  -- 'accumulation' | 'distribution' | 'neutral'
  classification_reason     TEXT,
  recorded_at               TEXT NOT NULL,
  PRIMARY KEY (ticker, week_start_date)
);

-- Per-ticker history (the panel's series) and the market-wide latest-week read
-- (the panel's current table) are the only two query shapes.
CREATE INDEX IF NOT EXISTS idx_equibles_ats_venue_share_ticker_week
  ON equibles_ats_venue_share (ticker, week_start_date DESC);

CREATE INDEX IF NOT EXISTS idx_equibles_ats_venue_share_week
  ON equibles_ats_venue_share (week_start_date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (41, datetime('now'));
