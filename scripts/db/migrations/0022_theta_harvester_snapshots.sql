-- 0022_theta_harvester_snapshots.sql — shared store for the Theta Harvester scan.
--
-- The theta scan previously wrote ONLY data/theta_harvester.json on whichever
-- host ran it, and the GET route read that host-local file. There is no theta
-- auto-scan timer, so on prod (where the Next.js host and the scan host can
-- differ) the page read a stale/empty file and showed zero candidates / the old
-- NDX universe. Mirroring the scan into Turso (like scanner_snapshots) makes the
-- latest scan a shared source of truth every host can read.
--
-- One row per scan, keyed by the payload's scan_time. INSERT OR REPLACE keeps it
-- idempotent; the GET reads ORDER BY scan_time DESC LIMIT 1.

CREATE TABLE IF NOT EXISTS theta_harvester_snapshots (
  scan_time TEXT PRIMARY KEY,
  payload   TEXT NOT NULL
);
