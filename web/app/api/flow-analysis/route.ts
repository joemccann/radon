import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getDb } from "@/lib/db";
import { cachedRead, invalidateCache } from "@/lib/dbCache";
import { snapshotCacheMeta } from "@/lib/snapshotCacheMeta";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders, scrubSecrets } from "@/lib/apiContracts";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const STALE_THRESHOLD_SECONDS = 3600;
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

/** Phase 2 — latest Turso snapshot, timestamped by the scan_time row key. */
async function readFlowAnalysisFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM flow_analysis_snapshots ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

function readFlowAnalysisSnapshot() {
  return cachedRead("flow-analysis:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readFlowAnalysisFromDb,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "flow-analysis",
    }),
  );
}

export const radonCapability = { GET: "read", POST: "read.spawn" };

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "flow-analysis:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const result = await readFlowAnalysisSnapshot();
  const cache_meta = snapshotCacheMeta(result.ok ? result.timestampMs : null, STALE_THRESHOLD_SECONDS);
  if (result.ok) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...result.data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({
      analysis_time: "",
      positions_scanned: 0,
      supports: [],
      against: [],
      watch: [],
      neutral: [],
      cache_meta,
    }),
    requestId,
  );
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    operatorOnly: true,
    rate: { key: "flow-analysis-run", limit: 2, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch<Record<string, unknown>>("/flow-analysis", { method: "POST", timeout: 130_000 });
    invalidateCache("flow-analysis:snapshot");
    const cache_meta = snapshotCacheMeta(contentTimestampMs(data.analysis_time), STALE_THRESHOLD_SECONDS);
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...data, cache_meta }),
      requestId,
    );
  } catch (error) {
    // Serve the Turso or last-in-memory snapshot on failure
    const cached = await readFlowAnalysisSnapshot();
    if (cached.ok) {
      const cache_meta = snapshotCacheMeta(cached.timestampMs, STALE_THRESHOLD_SECONDS);
      const res = NextResponse.json({ ...cached.data, cache_meta, is_stale: true });
      res.headers.set("X-Sync-Warning", "Radon API unavailable - serving cached data");
      return setNoStoreResponseHeaders(res, requestId);
    }
    const message = scrubSecrets(error instanceof Error ? error.message : "Flow analysis failed");
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
