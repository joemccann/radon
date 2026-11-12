import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, isMissingPayload, staleCollapse, type TimestampedRead } from "@/lib/dbFirstRead";
import { snapshotMaxAgeMs } from "@/lib/demo/snapshotWindow";
import { appendProvisionalRow, readLiveSnapshot } from "@/lib/provisionalRow";
import { isVixTsPoint, MISSING_VIXTS, type VixTsData } from "@/lib/vixts";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";


// radon-vixts.timer runs the Cboe VIX/VIX3M pull every 5 minutes in ET trading
// hours and daily at 16:01 ET on every calendar day — weekend and holiday runs
// are 304 heartbeats — so a snapshot older than 48h means the writer is down,
// not that the data is merely stale across a weekend.
const VIXTS_MAX_AGE_MS = 48 * 60 * 60_000;

async function readVixTsFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'vixts' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  const data = JSON.parse(row.payload) as Record<string, unknown>;
  // Freshness stays on the official snapshot; the provisional row only extends it.
  const merged = isMissingPayload(data) || !Array.isArray(data.series)
    ? data
    : appendProvisionalRow(
        data as unknown as VixTsData,
        await readLiveSnapshot((statement) => db.execute(statement), "vixts", isVixTsPoint),
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
    fromDb: readVixTsFromDb,
    maxAgeMs: await snapshotMaxAgeMs(VIXTS_MAX_AGE_MS),
    label: "vixts",
    // A fresher row that only carries the missing:true heartbeat must not
    // outrank an older row with a real series — source selection was on
    // timestamp alone and regressed the tab to the empty state. R-366.
    isDegraded: isMissingPayload,
  });
  // `result.fresh` was computed from VIXTS_MAX_AGE_MS and then DISCARDED, so
  // a dead radon-vixts.service kept serving a week-old snapshot with no stale
  // or missing marker and the panel rendered a confident regime badge for a
  // dead feed. Same shape as vixcor/route.ts. R-332.
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_VIXTS, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["vixts"],
  });
}
