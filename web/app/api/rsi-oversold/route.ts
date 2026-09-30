import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead, staleCollapse } from "@/lib/dbFirstRead";
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "rsi_oversold.json");

const MISSING_RSI_OVERSOLD = Object.freeze({
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [],
  threshold: null,
});

// radon-rsi-oversold.timer runs the sweep once daily at 23:05 UTC; 48h covers a
// full missed day plus slack. A snapshot older than that means the writer is
// down, not merely between runs.
const RSI_OVERSOLD_MAX_AGE_MS = 48 * 60 * 60_000;

async function readRsiOversoldFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'rsi-oversold' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readRsiOversoldFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readRsiOversoldFromDb,
    fromDisk: readRsiOversoldFromDisk,
    maxAgeMs: RSI_OVERSOLD_MAX_AGE_MS,
    label: "rsi-oversold",
  });
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_RSI_OVERSOLD, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["rsi-oversold"],
  });
}
