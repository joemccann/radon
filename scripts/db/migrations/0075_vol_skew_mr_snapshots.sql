-- 0075_vol_skew_mr_snapshots.sql — shared store for the Vol/Skew MR scanner.
--
-- Same host-local-cache bug as strength (see 0023): the scan writes
-- data/vol_skew_mr.json on whichever host ran it. Mirroring into Turso
-- makes the latest scan a shared source of truth.
--
-- One row per scan, keyed by the payload's scan_time. INSERT OR REPLACE
-- keeps it idempotent; the GET reads ORDER BY scan_time DESC LIMIT 30 and
-- picks a usable snapshot.

CREATE TABLE IF NOT EXISTS vol_skew_mr_snapshots (
  scan_time TEXT PRIMARY KEY,
  payload   TEXT NOT NULL
);
