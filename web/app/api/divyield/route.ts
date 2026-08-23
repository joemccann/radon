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

const CACHE_PATH = join(process.cwd(), "..", "data", "divyield.json");

// Contract: absent div-yield data is HTTP 200 with missing:true, never a 4xx.
// Defined locally on purpose: web/lib/divyield.ts belongs to the UI layer.
const MISSING_DIVYIELD = Object.freeze({
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [],
  backfill_cutover: null,
});

// radon-divyield.timer runs the sweep once daily at 22:40 UTC; 48h covers a
// full missed day plus slack. A snapshot older than that means the writer
// is down, not merely between runs.
const DIVYIELD_MAX_AGE_MS = 48 * 60 * 60_000;

async function readDivYieldFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'div-yield' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readDivYieldFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readDivYieldFromDb,
    fromDisk: readDivYieldFromDisk,
    maxAgeMs: DIVYIELD_MAX_AGE_MS,
    label: "divyield",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_DIVYIELD);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["divyield"],
  });
}
