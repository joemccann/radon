-- 0026_scan_snapshots.sql — generic latest-scan mirror for whole-file scans.
--
-- The low-cadence scans whose JSON caches were disk-only (leap-scan,
-- garch-scan, flow-surprise) get one shared Turso table so prod reads never
-- depend on the producer and the Next.js reader sharing a filesystem — a
-- producer moving hosts stranded app.radon.run on stale files once already
-- (the scanner/theta/strength incident). Keyed (service, scan_time); the
-- writer (upsert_scan_snapshot) prunes to the newest SCAN_SNAPSHOT_KEEP rows
-- per service so the table stays bounded.

CREATE TABLE IF NOT EXISTS scan_snapshots (
  service   TEXT NOT NULL,
  scan_time TEXT NOT NULL,
  payload   TEXT NOT NULL,
  PRIMARY KEY (service, scan_time)
);
