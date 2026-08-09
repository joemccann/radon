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

const CACHE_PATH = join(process.cwd(), "..", "data", "cor.json");

// Contract: absent cor data is HTTP 200 with missing:true, never a 4xx.
const MISSING_COR = {
  missing: true,
  scan_time: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
};

// radon-cor.timer fires daily at 02:20 UTC including weekends (Cboe
// re-touch days heartbeat via conditional GET), so a snapshot older than
// two days means the writer is down.
const COR_MAX_AGE_MS = 48 * 60 * 60_000;

async function readCorFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'cor' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readCorFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readCorFromDb,
    fromDisk: readCorFromDisk,
    maxAgeMs: COR_MAX_AGE_MS,
    label: "cor",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_COR);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["cor"],
  });
}
