import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { cachedRead } from "@/lib/dbCache";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isCoverageFailedScan, pickUsableScanSnapshot } from "@/lib/scanCoverage";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const STALE_THRESHOLD_SECONDS = 6 * 60 * 60;
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

type CacheMeta = {
  last_refresh: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_threshold_seconds: number;
};

function buildResultCacheMeta(timestampMs: number | null, fresh: boolean): CacheMeta {
  const ageSeconds = timestampMs == null ? null : Math.max(0, Math.round((Date.now() - timestampMs) / 1000));
  return {
    last_refresh: timestampMs == null ? null : new Date(timestampMs).toISOString(),
    age_seconds: ageSeconds,
    is_stale: !fresh,
    stale_threshold_seconds: STALE_THRESHOLD_SECONDS,
  };
}

export function emptyStrengthConfirmationPayload() {
  return {
    scan_time: "",
    source: "Unusual Whales + Radon regime caches",
    universe: "preset:ndx100",
    requested_tickers: [],
    tickers_scanned: 0,
    candidates_found: 0,
    confirmed_strength_count: 0,
    results: [],
  };
}

export const radonCapability = "read";

/** Latest Turso snapshot — shared across hosts, so a scan that ran on the
 *  FastAPI host is visible to the Next.js host (there is no strength auto-scan
 *  timer). */
async function readStrengthFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM strength_confirmation_snapshots ORDER BY scan_time DESC LIMIT 30`,
    args: [],
  });
  const picked = pickUsableScanSnapshot(
    result.rows as unknown as Array<{ scan_time: string; payload: string }>,
  );
  if (picked == null) return null;
  return {
    data: picked.data,
    timestampMs: contentTimestampMs(picked.scanTime),
  };
}

function readStrengthConfirmationSnapshot() {
  return cachedRead("scanner:strength", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readStrengthFromDb,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "strength-confirmation",
      isDegraded: (data) => isCoverageFailedScan(data),
    }),
  );
}

/** Turso or last-in-memory snapshot, for the scan route's failure path. */
export async function readStrengthConfirmationCache(): Promise<Record<string, unknown> | null> {
  const result = await readStrengthConfirmationSnapshot();
  return result.ok ? result.data : null;
}

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const result = await readStrengthConfirmationSnapshot();
  if (result.ok) {
    const cache_meta = buildResultCacheMeta(result.timestampMs, result.fresh);
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...result.data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({ ...emptyStrengthConfirmationPayload(), cache_meta: buildResultCacheMeta(null, false) }),
    requestId,
  );
}
