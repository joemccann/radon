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

const CACHE_PATH = join(process.cwd(), "..", "data", "credit_spread.json");

// Contract: absent credit-spread data is HTTP 200 with missing:true, never a 4xx.
const MISSING_CREDIT_SPREAD = {
  missing: true,
  scan_time: null,
  count: 0,
  series: [],
  current: null,
};

// The refresh timer runs daily (21:45 UTC; weekend/holiday runs heartbeat
// with unchanged data), so a snapshot older than two days means the writer
// is down.
const CREDIT_SPREAD_MAX_AGE_MS = 48 * 60 * 60_000;

async function readCreditSpreadFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'credit-spread' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readCreditSpreadFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readCreditSpreadFromDb,
    fromDisk: readCreditSpreadFromDisk,
    maxAgeMs: CREDIT_SPREAD_MAX_AGE_MS,
    label: "credit-spread",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_CREDIT_SPREAD);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["credit-spread"],
  });
}
