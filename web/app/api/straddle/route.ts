import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";


// Contract: absent straddle data is HTTP 200 with missing:true, never a 4xx.
const MISSING_STRADDLE = {
  missing: true,
  scan_time: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
};

// radon-straddle.timer fires daily at 02:15 UTC including weekends (Cboe
// re-touch days heartbeat via conditional GET), so a snapshot older than
// two days means the writer is down.
const STRADDLE_MAX_AGE_MS = 48 * 60 * 60_000;

async function readStraddleFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'straddle' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readStraddleFromDb,
    maxAgeMs: STRADDLE_MAX_AGE_MS,
    label: "straddle",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_STRADDLE);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["straddle"],
  });
}
