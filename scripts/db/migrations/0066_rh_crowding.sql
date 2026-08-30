-- 0066_rh_crowding.sql — Robinhood retail-crowding overlay.
--
-- One row per (date, symbol): membership/rank in Robinhood's popular
-- watchlists plus how many Robinhood scans surfaced the symbol that day,
-- pulled read-only from the official trading MCP
-- (https://agent.robinhood.com/mcp/trading) by scripts/fetch_rh_crowding.py.
--
-- This series is a DESCRIPTIVE crowding overlay only. It must never feed the
-- Four Gates: it is not a dark-pool/OTC edge signal (Gate 2), plays no part
-- in convexity math (Gate 1), and never enters Kelly sizing (Gate 3).
-- Pinned by scripts/tests/test_rh_crowding.py::TestCrowdingCannotTripGates.

CREATE TABLE IF NOT EXISTS rh_crowding (
  date         TEXT NOT NULL,   -- YYYY-MM-DD (UTC fetch date)
  symbol       TEXT NOT NULL,
  popular_rank INTEGER,         -- 1-based rank in the popular watchlist; NULL = absent
  watchlists   TEXT,            -- JSON array of Robinhood watchlist names carrying the symbol
  scan_hits    INTEGER NOT NULL DEFAULT 0,  -- Robinhood scans surfacing the symbol
  recorded_at  TEXT NOT NULL,
  PRIMARY KEY (date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_rh_crowding_symbol_date
  ON rh_crowding (symbol, date DESC);

CREATE INDEX IF NOT EXISTS idx_rh_crowding_date
  ON rh_crowding (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (66, datetime('now'));
