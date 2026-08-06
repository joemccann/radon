-- 0035_perf_twr.sql — Performance TWR data model (plan §4.2)
--
-- GIPS-aligned daily TWR: nav_snapshots = Flex EquitySummaryInBase per account/day,
-- external_flows = classified deposits/withdrawals/ACATS per account/day,
-- twr_subperiods = chained daily subperiods derived from (B,E,C) with cum_r.
--
-- Flex queries:
--  1) EquitySummaryInBase by reportDate (IB_FLEX_NAV_QUERY_ID) → nav_snapshots
--  2) CashTransactions + Transfers (IB_FLEX_FLOWS_QUERY_ID) → external_flows
-- ACATS: Transfer where assetCategory IS NULL and no executed_order fill that day
--
-- No Highcharts scrape, no UW/Yahoo, no ib_twr_series.json.

CREATE TABLE IF NOT EXISTS nav_snapshots (
  account_id    TEXT NOT NULL,  -- U123456 or 'ALL' for consolidated
  report_date   TEXT NOT NULL,  -- YYYY-MM-DD ET
  total_net_liq REAL NOT NULL,  -- EquitySummaryByReportDateInBase.total
  cash          REAL,
  stock         REAL,
  options       REAL,
  accrued_fees  REAL,
  PRIMARY KEY (account_id, report_date)
);

CREATE INDEX IF NOT EXISTS idx_nav_snapshots_date
  ON nav_snapshots (report_date DESC);

CREATE TABLE IF NOT EXISTS external_flows (
  account_id  TEXT NOT NULL,
  report_date TEXT NOT NULL,    -- YYYY-MM-DD ET
  amount      REAL NOT NULL,    -- +deposit, -withdrawal, +ACATS NAV delta
  flow_type   TEXT NOT NULL,    -- deposit|withdrawal|acats|internal
  note        TEXT,
  PRIMARY KEY (account_id, report_date, flow_type)
);

CREATE INDEX IF NOT EXISTS idx_external_flows_date
  ON external_flows (report_date DESC);

CREATE TABLE IF NOT EXISTS twr_subperiods (
  account_id  TEXT NOT NULL,
  report_date TEXT NOT NULL,    -- YYYY-MM-DD ET
  b           REAL NOT NULL,    -- beginning NAV (total_net_liq_{t-1})
  e           REAL NOT NULL,    -- ending NAV (total_net_liq_t)
  c           REAL NOT NULL,    -- sum external flows on date t
  r           REAL NOT NULL,    -- (E - B - C)/B, or E/B-1 if C==0; 0 if B==0
  cum_r       REAL NOT NULL,    -- chained cum through t: Π(1+r_i)-1
  PRIMARY KEY (account_id, report_date)
);

CREATE INDEX IF NOT EXISTS idx_twr_subperiods_date
  ON twr_subperiods (report_date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (35, datetime('now'));
