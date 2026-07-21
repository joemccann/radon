-- 0029_rv_ratio.sql — RV Ratio indicator (tasks/rv-ratio-indicator-plan.md).
--
-- price_history_daily: durable cross-host store of daily closes (split-
-- adjusted, dividend-UNadjusted) shared across assets — SPY is fetched once
-- per day and reused by every asset scan. Deliberately NOT the
-- data/price_history_cache/ request cache, whose TTLs and LRU prune would
-- force daily deep re-fetches and could evict SPY.
--
-- rv_ratio_snapshots: latest snapshot per symbol (INSERT OR REPLACE), the
-- payload being the schema_version 1 contract from the plan's section 6.
-- Not scan_snapshots: its (service, scan_time, payload) keying doesn't fit
-- per-symbol single-row snapshots; this is a standalone writer family in
-- scripts/db/writer.py (upsert_price_history_rows, upsert_rv_ratio_snapshot).

CREATE TABLE IF NOT EXISTS price_history_daily (
  symbol     TEXT NOT NULL,
  date       TEXT NOT NULL,   -- YYYY-MM-DD, ET session date
  close      REAL NOT NULL,   -- split-adjusted, dividend-UNadjusted
  source     TEXT NOT NULL,   -- 'ib' | 'uw' | 'yahoo'
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS rv_ratio_snapshots (
  symbol   TEXT PRIMARY KEY,  -- latest snapshot per symbol, INSERT OR REPLACE
  taken_at TEXT NOT NULL,
  payload  TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (29, datetime('now'));
