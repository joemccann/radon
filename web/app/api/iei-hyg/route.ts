import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead, staleCollapse, isMissingPayload } from "@/lib/dbFirstRead";
import { MISSING_IEI_HYG } from "@/lib/ieiHyg";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "iei_hyg.json");

// The refresh timer runs daily (21:55 UTC; weekend/holiday runs heartbeat
// with unchanged data), so a snapshot older than two days means the writer
// is down.
const IEI_HYG_MAX_AGE_MS = 48 * 60 * 60_000;

async function readIeiHygFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'iei-hyg' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readIeiHygFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readIeiHygFromDb,
    fromDisk: readIeiHygFromDisk,
    maxAgeMs: IEI_HYG_MAX_AGE_MS,
    label: "iei-hyg",
    // R-193: a writer that ran, produced nothing and still stamped a
    // timestamped row would otherwise outrank an older row with a real
    // series on freshness alone. Same guard vixcor and ivrank carry.
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_IEI_HYG, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["iei-hyg"],
  });
}
