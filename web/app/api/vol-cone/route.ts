import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead, staleCollapse, isMissingPayload } from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "vol_cone.json");

// Contract: absent vol-cone data is HTTP 200 with missing:true, never a 4xx.
const MISSING_VOL_CONE = {
  missing: true,
  scan_time: null,
  source_as_of: null,
  count: 0,
  hit_count: 0,
  current: null,
  names: [],
  hits: [],
};

// radon-vol-cone-intraday.timer refreshes the snapshot every 15m during ET
// trading hours and radon-vol-cone.timer writes the completed session at
// 20:45 UTC Mon-Fri. Two days is the overnight/weekend floor: older than
// that and both writers are down.
const MAX_AGE_MS = 48 * 60 * 60_000;

async function readVolConeFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'vol-cone' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readVolConeFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readVolConeFromDb,
    fromDisk: readVolConeFromDisk,
    maxAgeMs: MAX_AGE_MS,
    label: "vol-cone",
    // R-193: a writer that ran, produced nothing and still stamped a
    // timestamped row would otherwise outrank an older row with a real
    // series on freshness alone. Same guard vixcor and ivrank carry.
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_VOL_CONE, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["vol-cone"],
  });
}
