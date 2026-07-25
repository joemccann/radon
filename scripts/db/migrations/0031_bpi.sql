-- 0031_bpi.sql — Bullish Percent Index indicator (tasks/bpi-indicator-plan.md §4).
--
-- bpi_history: durable per-session BPI per index — bpi = 100 * bullish /
-- members, where members counts only constituents with a RESOLVED P&F
-- signal that session (scripts/utils/pnf.py).
--
-- bpi_snapshots: latest schema_version 1 payload per index (INSERT OR
-- REPLACE), the Turso-first read for the Next.js /api/bpi route. Same
-- standalone writer-family shape as rv_ratio_snapshots (0029).

CREATE TABLE IF NOT EXISTS bpi_history (
  index_symbol TEXT NOT NULL,   -- 'NDX' | 'SPX' | 'RUT'
  date         TEXT NOT NULL,   -- YYYY-MM-DD, ET session date
  bpi          REAL NOT NULL,
  members      INTEGER NOT NULL,
  bullish      INTEGER NOT NULL,
  PRIMARY KEY (index_symbol, date)
);

CREATE TABLE IF NOT EXISTS bpi_snapshots (
  index_symbol TEXT PRIMARY KEY,
  taken_at     TEXT NOT NULL,
  payload      TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (31, datetime('now'));
