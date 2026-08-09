-- 0039_cor.sql — COR indicator: Cboe SPX implied correlation index family
-- (COR1M/COR3M/COR6M/COR1Y), one row per session over the union of tenor
-- dates (2006-01-03+). Tenor columns are NULL where that tenor is missing
-- the date (COR3M has 15 scattered gaps the other tenors cover).

CREATE TABLE IF NOT EXISTS cor_history (
  date        TEXT PRIMARY KEY,
  cor1m       REAL,
  cor3m       REAL,
  cor6m       REAL,
  cor1y       REAL,
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cor_history_date ON cor_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (39, datetime('now'));
