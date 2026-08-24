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

const CACHE_PATH = join(process.cwd(), "..", "data", "hhlev.json");

// Contract: absent household-leverage data is HTTP 200 with missing:true,
// never a 4xx. Defined locally on purpose: web/lib/hhlev.ts belongs to the
// UI layer.
const MISSING_HHLEV = Object.freeze({
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  current: null,
  series: [],
});

// Quarterly source but DAILY writer heartbeat (radon-hhlev.timer runs the
// cheap conditional check every day at 13:20 UTC): a snapshot older than
// 48h means the writer is down, not the data stale.
const HHLEV_MAX_AGE_MS = 48 * 60 * 60_000;

async function readHhLevFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'hhlev' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readHhLevFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readHhLevFromDb,
    fromDisk: readHhLevFromDisk,
    maxAgeMs: HHLEV_MAX_AGE_MS,
    label: "hhlev",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_HHLEV);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["hhlev"],
  });
}
