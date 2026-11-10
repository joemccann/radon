import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getFreshnessWindowMs } from "@/lib/serviceHealthWindows";
import type { MsrData } from "@/lib/msr";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

// Contract: absent MSR data is HTTP 200 with missing:true, never a 4xx.
const MISSING_MSR = {
  missing: true,
  scan_time: null,
  start_date: null,
  indexes: null,
  series: [],
};

// radon-msr.timer fires daily including weekends, so the shared catalog's
// closed window is the serve budget for a live writer.
const MSR_MAX_AGE_MS = getFreshnessWindowMs("msr", "closed");

async function readMsrFromDb(): Promise<TimestampedRead<MsrData> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'msr' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as MsrData,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readMsrFromDb,
    maxAgeMs: MSR_MAX_AGE_MS,
    label: "msr",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_MSR);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["msr"],
  });
}
