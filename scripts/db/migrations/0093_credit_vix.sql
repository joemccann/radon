-- 0093_credit_vix.sql — CREDIT/VIX indicator: daily SHY, HYG and VIX closes
-- (spread / ranks / gap are derived, not stored). Sources: SHY/HYG via the
-- iei-hyg equity cascade; VIX via IB Index -> Cboe CDN -> Yahoo.

CREATE TABLE IF NOT EXISTS credit_vix_history (
  date        TEXT PRIMARY KEY,
  shy_close   REAL NOT NULL,
  hyg_close   REAL NOT NULL,
  vix_close   REAL NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credit_vix_history_date_desc ON credit_vix_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (93, datetime('now'));
