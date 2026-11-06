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

async function readDiscoverFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM discover_snapshots ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    // Hetzner writes scan_time via Python `datetime.now().isoformat()`,
    // which is naive (no offset). JS `Date.parse` treats naive ISO as
    // local time, shifting the instant by the viewer's UTC offset and
    // making the freshness banner lie. contentTimestampMs treats naive
    // strings as UTC.
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

function readDiscoverSnapshot() {
  return cachedRead("discover:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readDiscoverFromDb,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "discover",
    }),
  );
}

export const radonCapability = { GET: "read", POST: "read.spawn" };

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "discover:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const result = await readDiscoverSnapshot();
  const cache_meta = snapshotCacheMeta(result.ok ? result.timestampMs : null, STALE_THRESHOLD_SECONDS);
  if (result.ok) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...result.data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({
      discovery_time: "",
      alerts_analyzed: 0,
      candidates_found: 0,
      candidates: [],
      cache_meta,
    }),
    requestId,
  );
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "discover:route", limit: 20, windowMs: 60_000 }, durableRateTier: "B" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch<Record<string, unknown>>("/discover", { method: "POST", timeout: 130_000 });
    invalidateCache("discover:snapshot");
    const cache_meta = snapshotCacheMeta(contentTimestampMs(data.discovery_time), STALE_THRESHOLD_SECONDS);
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...data, cache_meta }),
      requestId,
    );
  } catch (error) {
    // Serve the Turso or last-in-memory snapshot on failure
    const cached = await readDiscoverSnapshot();
    if (cached.ok) {
      const cache_meta = snapshotCacheMeta(cached.timestampMs, STALE_THRESHOLD_SECONDS);
      const res = NextResponse.json({ ...cached.data, cache_meta, is_stale: true });
      res.headers.set("X-Sync-Warning", "Radon API unavailable - serving cached data");
      return setNoStoreResponseHeaders(res, requestId);
    }
    const message = scrubSecrets(error instanceof Error ? error.message : "Discover sync failed");
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
