import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { join } from "path";
import { getDb } from "@/lib/db";
import { cachedRead } from "@/lib/dbCache";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch } from "@/lib/radonApi";
import { backfillThetaEarningsPayload } from "@/lib/thetaEarningsBackfill";
import { isCoverageFailedScan, pickUsableScanSnapshot } from "@/lib/scanCoverage";
import { buildDemoThetaHarvester } from "@/lib/demo/fixtures/thetaHarvester";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const STALE_THRESHOLD_SECONDS = 6 * 60 * 60;
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;
/** UW batch annotate budget for pre-feature snapshot backfill. */
const EARNINGS_BACKFILL_TIMEOUT_MS = 90_000;

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

export function emptyThetaHarvesterPayload() {
  return {
    scan_time: "",
    source: "Unusual Whales",
    universe: "preset:ndx100",
    requested_tickers: [],
    tickers_scanned: 0,
    candidates_found: 0,
    theta_harvest_count: 0,
    results: [],
  };
}

export const radonCapability = "read";

/** Latest Turso snapshot — shared across hosts, so the scan that
 *  radon-signals-refresh.timer runs on the FastAPI host is visible to the
 *  Next.js host. */
async function readThetaFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM theta_harvester_snapshots ORDER BY scan_time DESC LIMIT 30`,
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

async function fetchEarningsBatch(tickers: string[]): Promise<{
  results?: Array<Record<string, unknown>>;
} | null> {
  const path = `/earnings?tickers=${encodeURIComponent(tickers.join(","))}`;
  return radonFetch<{ results?: Array<Record<string, unknown>> }>(path, {
    timeout: EARNINGS_BACKFILL_TIMEOUT_MS,
  });
}

function readThetaHarvesterSnapshot() {
  return cachedRead("scanner:theta", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readThetaFromDb,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "theta-harvester",
      isDegraded: (data) => isCoverageFailedScan(data),
    }),
  );
}

/** Turso or last-in-memory snapshot, for the scan route's failure path. */
export async function readThetaHarvesterCache(): Promise<Record<string, unknown> | null> {
  const result = await readThetaHarvesterSnapshot();
  return result.ok ? result.data : null;
}

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "scanner/theta:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  if (access.principal.kind === "demo") {
    const now = new Date();
    return setNoStoreResponseHeaders(
      NextResponse.json({
        ...buildDemoThetaHarvester({ now }),
        cache_meta: {
          last_refresh: now.toISOString(),
          age_seconds: 0,
          is_stale: false,
          stale_threshold_seconds: STALE_THRESHOLD_SECONDS,
        },
      }),
      requestId,
    );
  }
  const result = await readThetaHarvesterSnapshot();
  if (result.ok) {
    const cache_meta = buildResultCacheMeta(result.timestampMs, result.fresh);
    // Pre-feature snapshots lack the `earnings` key. Backfill from the
    // standalone earnings service so the column is not blank until the next
    // full NDX rescan. Fail-open: backfill errors leave the payload unchanged.
    const data = await backfillThetaEarningsPayload(
      result.data as Record<string, unknown>,
      fetchEarningsBatch,
    );
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({ ...emptyThetaHarvesterPayload(), cache_meta: buildResultCacheMeta(null, false) }),
    requestId,
  );
}
