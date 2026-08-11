import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const SERVICE = "equibles-ats-venue-share";
const CACHE_PATH = join(process.cwd(), "..", "data", "equibles_ats_venue_share.json");

// Contract: absent venue-share data is HTTP 200 with missing:true, never a 4xx.
const MISSING_ATS_VENUE_SHARE = {
  missing: true,
  scan_time: null,
  count: 0,
  tickers: [],
  week_start_date: null,
  thresholds: null,
  current: [],
  series: {},
  errors: [],
};

// The upstream FINRA file advances once a week; the refresh runs daily so the
// heartbeat stays honest. A snapshot older than three days means the writer is
// down, not that the source was quiet.
const ATS_VENUE_SHARE_MAX_AGE_MS = 72 * 60 * 60_000;

async function readVenueShareFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
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

async function readVenueShareFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readVenueShareFromDb,
    fromDisk: readVenueShareFromDisk,
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
