-- 0002_cash_flows.sql — record IBKR cash transactions (deposits, withdrawals).
--
-- Why a dedicated table: the existing `journal` table tracks executions
-- (option/stock fills) only. Cash transactions live in a separate IB Flex
-- Query section (`CashTransaction`) and need their own surface so the
-- /orders page can show the user a complete picture of capital movement.
--
-- Source: scripts/cash_flow_sync.py pulls IB_FLEX_NAV_QUERY_ID and parses
-- CashTransaction rows. Deposits arrive with positive `amount`, withdrawals
-- with negative. Idempotent on `id` (IB transactionID) so re-running the
-- pull is a no-op.

CREATE TABLE IF NOT EXISTS cash_flows (
  id           TEXT PRIMARY KEY,             -- IB transactionID
  date         TEXT NOT NULL,                -- ISO date YYYY-MM-DD (reportDate)
  type         TEXT NOT NULL,                -- 'Deposit' | 'Withdrawal' | 'Dividend' | 'Interest' | 'Fee' | 'Other'
  amount       REAL NOT NULL,                -- signed: positive=inflow, negative=outflow
  currency     TEXT NOT NULL DEFAULT 'USD',
  description  TEXT,                         -- IB's free-text description
  raw_type     TEXT,                         -- IB's raw `type` attribute (for debugging)
  synced_at    TEXT NOT NULL                 -- ISO timestamp when this row was last touched
);

CREATE INDEX IF NOT EXISTS cash_flows_date_idx
  ON cash_flows (date DESC);
