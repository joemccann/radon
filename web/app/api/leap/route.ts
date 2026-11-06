import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { cachedRead } from "@/lib/dbCache";
import { dbExecute } from "@/lib/dbExecute";
import { snapshotCacheMeta } from "@/lib/snapshotCacheMeta";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

/**
 * GET /api/leap
 *
 * Serves the latest LEAP IV-mispricing scan from the Turso scan_snapshots row
 * (service "leap-scan", mirrored by leap_scanner_uw.py), or the last good read
 * held in memory when Turso fails.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const STALE_THRESHOLD_SECONDS = 6 * 60 * 60; // LEAP scans are slow + low-cadence
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

async function readLeapFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'leap-scan' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  }, { label: "leap" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const result = await cachedRead("leap:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readLeapFromDb,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "leap",
    }),
  );
  const cache_meta = snapshotCacheMeta(result.ok ? result.timestampMs : null, STALE_THRESHOLD_SECONDS);
  if (result.ok) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...result.data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({
      scan_time: "",
      min_gap: null,
      results: [],
      cache_meta,
    }),
    requestId,
  );
}
