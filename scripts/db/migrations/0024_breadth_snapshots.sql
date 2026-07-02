-- 0024_breadth_snapshots.sql — NYSE advance/decline market-breadth scan.
--
-- Mirrors cri_snapshots: one row per (ET session date, scan timestamp) so the
-- newest row per day is a cheap indexed lookup and every host reads the same
-- latest scan (the data/breadth.json cache is host-local).

CREATE TABLE IF NOT EXISTS breadth_snapshots (
  date        TEXT    NOT NULL,           -- ET trading day, YYYY-MM-DD
  taken_at    TEXT    NOT NULL,           -- ISO-8601 UTC
  payload     TEXT    NOT NULL,
  PRIMARY KEY (date, taken_at)
);
CREATE INDEX IF NOT EXISTS idx_breadth_latest ON breadth_snapshots(date DESC, taken_at DESC);
