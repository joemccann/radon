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

const CACHE_PATH = join(process.cwd(), "..", "data", "yield_curve.json");

// Contract: absent yield-curve data is HTTP 200 with missing:true, never a 4xx.
const MISSING_YIELD_CURVE = {
  missing: true,
  scan_time: null,
  count: 0,
  series: [],
  current: null,
};

// The refresh timer runs daily (22:30 UTC; weekend/holiday runs heartbeat
// with unchanged data), so a snapshot older than two days means the writer
// is down.
const YIELD_CURVE_MAX_AGE_MS = 48 * 60 * 60_000;

async function readYieldCurveFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'yield-curve' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readYieldCurveFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readYieldCurveFromDb,
    fromDisk: readYieldCurveFromDisk,
    maxAgeMs: YIELD_CURVE_MAX_AGE_MS,
    label: "yield-curve",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_YIELD_CURVE);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["yield-curve"],
  });
}
