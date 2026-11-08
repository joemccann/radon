-- 0110_quant_execution.sql — quant sleeve execution bridge (docs/quant-strategies.md §8).
--
-- quant_targets gains the session close the weights were computed on (the
-- bridge sizes shares off it) and the rebalance flag the 16:25 ET run sets,
-- so the 09:35 ET bridge trades only on rebalance sessions.
-- quant_orders is the sleeve's own order log: one row per symbol per
-- placement, including dry-run plans, so the tab shows what the bridge did
-- or would have done. It never substitutes for the broker ledger.

ALTER TABLE quant_targets ADD COLUMN close REAL;
ALTER TABLE quant_targets ADD COLUMN rebalance_today INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS quant_orders (
  strategy    TEXT    NOT NULL,
  as_of       TEXT    NOT NULL,          -- targets session the order serves
  symbol      TEXT    NOT NULL,
  action      TEXT    NOT NULL CHECK (action IN ('BUY', 'SELL')),
  quantity    INTEGER NOT NULL CHECK (quantity > 0),
  limit_price REAL    NOT NULL,
  order_ref   TEXT    NOT NULL,          -- 'quant:<strategy>', the sleeve's fill attribution
  status      TEXT    NOT NULL CHECK (status IN ('planned', 'ok', 'error', 'indeterminate', 'skipped')),
  order_id    INTEGER,
  perm_id     INTEGER,
  message     TEXT,
  placed_at   TEXT    NOT NULL,
  PRIMARY KEY (strategy, placed_at, symbol)
);

CREATE INDEX IF NOT EXISTS idx_quant_orders_latest ON quant_orders(strategy, as_of DESC, placed_at DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (110, datetime('now'));
