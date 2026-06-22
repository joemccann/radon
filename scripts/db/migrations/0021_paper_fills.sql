-- 0021_paper_fills.sql — F13 paper-trading / shadow-fill engine.
--
-- A paper-namespaced fill store, deliberately SEPARATE from the IB-rehydrated
-- `journal` table so simulated fills never contaminate real positions / P&L.
-- One row per simulated fill. `account` tags the paper book (default
-- 'PAPER') and `source` records which engine produced it ('shadow' for the
-- homegrown matcher; 'ibkr-paper' if later wired to the IBKR native paper
-- account on port 7497). The full fill payload is stored verbatim as JSON.

CREATE TABLE IF NOT EXISTS paper_fills (
  fill_id     TEXT    PRIMARY KEY,         -- caller-supplied idempotency key
  account     TEXT    NOT NULL,            -- paper book tag, e.g. 'PAPER'
  source      TEXT    NOT NULL,            -- 'shadow' | 'ibkr-paper'
  ticker      TEXT    NOT NULL,
  side        TEXT    NOT NULL,            -- BUY | SELL
  order_type  TEXT    NOT NULL,            -- LIMIT | STOP
  quantity    REAL    NOT NULL,
  fill_price  REAL    NOT NULL,            -- per-share executed price
  filled_at   TEXT    NOT NULL,            -- ISO-8601 UTC
  payload     TEXT    NOT NULL,            -- full fill JSON (contract, costs)
  written_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_fills_account
  ON paper_fills(account, filled_at DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (21, datetime('now'));
