import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { cachedRead } from "@/lib/dbCache";
import { contentTimestampMs, dbFirstRead, isMissingPayload, type TimestampedRead } from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const SERVICE = "equibles-ats-venue-share";

// Contract: absent venue-share data is HTTP 200 with missing:true, never a 4xx.
const MISSING_ATS_VENUE_SHARE = {
  missing: true,
  scan_time: null,
  count: 0,
  tickers: [],
  week_start_date: null,
  thresholds: null,
  current: [],
  errors: [],
};

// The upstream FINRA file advances once a week; the refresh runs daily so the
// heartbeat stays honest. A snapshot older than three days means the writer is
// down, not that the source was quiet.
const ATS_VENUE_SHARE_MAX_AGE_MS = 72 * 60 * 60_000;

// radon-equibles-ats.timer writes one snapshot a week (Tue 09:15 UTC) and the
// panel polls hourly, so one read per hour per process is still far ahead of
// the writer.
const READ_CACHE_TTL_MS = 60 * 60_000;

type VenueShareRead = TimestampedRead<Record<string, unknown>>;

// The writer no longer stores the per-ticker `series` map in scan_snapshots (it
// lives in equibles_ats_venue_share). Older rows still carry it (53.65 MB of a
// 54.2 MB row) and no view reads it, so json_remove drops it inside Turso.

function isCacheableRead(read: VenueShareRead | null): boolean {
  if (!read || isMissingPayload(read.data)) return false;
  const current = read.data.current;
  return Array.isArray(current) && current.length > 0;
}

async function readVenueShareFromDb(): Promise<VenueShareRead | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, json_remove(payload, '$.series') AS payload FROM scan_snapshots
          WHERE service = ? ORDER BY scan_time DESC LIMIT 1`,
    args: [SERVICE],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

function cachedSource(source: string, read: () => Promise<VenueShareRead | null>) {
  return () => cachedRead(`scan:${SERVICE}:${source}`, READ_CACHE_TTL_MS, read, {
    shouldCache: isCacheableRead,
  });
}

const readCachedVenueShareFromDb = cachedSource("db", readVenueShareFromDb);

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readCachedVenueShareFromDb,
    maxAgeMs: ATS_VENUE_SHARE_MAX_AGE_MS,
    label: SERVICE,
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_ATS_VENUE_SHARE);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: [SERVICE],
  });
}
