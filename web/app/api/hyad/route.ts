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

const CACHE_PATH = join(process.cwd(), "..", "data", "hyad.json");

// Contract: absent HY A-D data is HTTP 200 with missing:true, never a 4xx.
// Defined locally on purpose: web/lib/hyad.ts belongs to the UI layer.
const MISSING_HYAD = Object.freeze({
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [],
});

// radon-hyad.timer runs the pull Tue..Sat 11:00 UTC — FINRA TRACE publishes
// T+1, so 120h covers the T+1 lag plus 3-day weekends and bond-market-only
// holidays. A snapshot older than that means the writer is down, not merely
// between runs.
const HYAD_MAX_AGE_MS = 120 * 60 * 60_000;

async function readHyAdFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'hy-ad' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readHyAdFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readHyAdFromDb,
    fromDisk: readHyAdFromDisk,
    maxAgeMs: HYAD_MAX_AGE_MS,
    label: "hyad",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_HYAD);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["hyad"],
  });
}
