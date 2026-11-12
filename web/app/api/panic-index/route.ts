import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, isMissingPayload, staleCollapse, type TimestampedRead } from "@/lib/dbFirstRead";
import { snapshotMaxAgeMs } from "@/lib/demo/snapshotWindow";
import { isPanicIndexPoint, MISSING_PANIC_INDEX, type PanicIndexData } from "@/lib/panicIndex";
import { appendProvisionalRow, readLiveSnapshot } from "@/lib/provisionalRow";
import { getFreshnessWindowMs } from "@/lib/serviceHealthWindows";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";


// radon-panic-index.timer fires every 5 minutes in ET trading hours, plus 16:01
// ET and 13:15 UTC every calendar day —
// weekend and holiday runs are 304 heartbeats — so a snapshot older than the
// catalog's uniform 26h window means the writer is down. Shared catalog, not
// a private constant (SKILL.md §3; vol-cone e7323e4e).
const PANIC_INDEX_MAX_AGE_MS = getFreshnessWindowMs("panic-index", "closed");

async function readPanicIndexFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'panic-index' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  const data = JSON.parse(row.payload) as Record<string, unknown>;
  // Freshness stays on the official snapshot; the provisional row only extends it.
  const merged = isMissingPayload(data) || !Array.isArray(data.series)
    ? data
    : appendProvisionalRow(
        data as unknown as PanicIndexData,
        await readLiveSnapshot((statement) => db.execute(statement), "panic-index", isPanicIndexPoint),
      );
  return {
    data: merged as unknown as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readPanicIndexFromDb,
    maxAgeMs: await snapshotMaxAgeMs(PANIC_INDEX_MAX_AGE_MS),
    label: "panic-index",
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_PANIC_INDEX, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["panic-index"],
  });
}
