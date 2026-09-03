import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, isMissingPayload, type TimestampedRead, staleCollapse } from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "iv_spread.json");

// Contract: absent iv-spread data is HTTP 200 with missing:true, never a 4xx.
// The job's own degradation state ("stale_source") is a real payload and
// passes straight through; only "no snapshot anywhere" and "every snapshot
// older than the freshness budget" collapse to this shape.
const MISSING_IV_SPREAD = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
  excluded: [],
};

// radon-iv-spread.timer fires daily at 22:15 UTC including weekends, so a
// snapshot older than two days means the writer is down.
const IV_SPREAD_MAX_AGE_MS = 48 * 60 * 60_000;

// REL-214 (R-576): a stale_source re-serve refreshes scan_time on every run,
// so freshness must ride the DATA age (as_of, a YYYY-MM-DD) or the 48h gate
// can never trip during a long IB outage — machine consumers saw 10-day-old
// data with a live scan_time. Non-stale payloads keep scan_time freshness.
function ivSpreadContentTimestampMs(data: Record<string, unknown>, scanTime: unknown): number | null {
  if (data.status === "stale_source" && typeof data.as_of === "string" && data.as_of) {
    return contentTimestampMs(`${data.as_of}T22:15:00Z`);
  }
  return contentTimestampMs(scanTime as string);
}

async function readIvSpreadFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'iv-spread' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  const data = JSON.parse(row.payload) as Record<string, unknown>;
  return { data, timestampMs: ivSpreadContentTimestampMs(data, row.scan_time) };
}

async function readIvSpreadFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: ivSpreadContentTimestampMs(data, data.scan_time) };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readIvSpreadFromDb,
    fromDisk: readIvSpreadFromDisk,
    maxAgeMs: IV_SPREAD_MAX_AGE_MS,
    label: "iv-spread",
    // A fresher row that only carries the missing:true heartbeat must not
    // outrank an older row with a real series. The job's "stale_source"
    // payload is NOT degraded by this test: it carries a full series and
    // reaches the client verbatim.
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_IV_SPREAD, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["iv-spread"],
  });
}
