import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getDb } from "@/lib/db";
import { cachedRead, invalidateCache } from "@/lib/dbCache";
import { snapshotCacheMeta } from "@/lib/snapshotCacheMeta";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, scrubSecrets, setNoStoreResponseHeaders } from "@/lib/apiContracts";
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
async function readScannerFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scanner_snapshots ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

function readScannerSnapshot() {
  return cachedRead("scanner:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readScannerFromDb,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "scanner",
    }),
  );
}

export const radonCapability = { GET: "read", POST: "read.spawn" };

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "scanner:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const result = await readScannerSnapshot();
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
      tickers_scanned: 0,
      signals_found: 0,
      top_signals: [],
      cache_meta,
    }),
    requestId,
  );
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "scanner:route", limit: 20, windowMs: 60_000 }, durableRateTier: "B" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch<Record<string, unknown>>("/scan", { method: "POST", timeout: 130_000 });
    invalidateCache("scanner:snapshot");
    const cache_meta = snapshotCacheMeta(contentTimestampMs(data.scan_time), STALE_THRESHOLD_SECONDS);
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...data, cache_meta }),
      requestId,
    );
  } catch (error) {
    // Serve the Turso or last-in-memory snapshot on failure
    const cached = await readScannerSnapshot();
    if (cached.ok) {
      const cache_meta = snapshotCacheMeta(cached.timestampMs, STALE_THRESHOLD_SECONDS);
      const res = NextResponse.json({ ...cached.data, cache_meta, is_stale: true });
      res.headers.set("X-Sync-Warning", "Radon API unavailable - serving cached data");
      return setNoStoreResponseHeaders(res, requestId);
    }
    // RC-B9: upstream error text can carry a LibsqlError's URL + token.
    const message = scrubSecrets(error instanceof Error ? error.message : "Scanner failed");
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
