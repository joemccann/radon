-- 0023_strength_confirmation_snapshots.sql — shared store for the 7-Step
-- Strength Confirmation scan.
--
-- Same host-local-cache bug as theta (see 0022): the scan wrote ONLY
-- data/strength_confirmation.json on whichever host ran it, and the GET route
-- read that host-local file. With no auto-scan timer, prod (Next.js host != scan
-- host) read a stale/empty file and showed zero candidates / the old universe.
-- Mirroring into Turso makes the latest scan a shared source of truth.
--
-- One row per scan, keyed by the payload's scan_time. INSERT OR REPLACE keeps it
-- idempotent; the GET reads ORDER BY scan_time DESC LIMIT 1.

CREATE TABLE IF NOT EXISTS strength_confirmation_snapshots (
  scan_time TEXT PRIMARY KEY,
  payload   TEXT NOT NULL
);
