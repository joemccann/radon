import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead, staleCollapse, isMissingPayload } from "@/lib/dbFirstRead";
import { snapshotMaxAgeMs } from "@/lib/demo/snapshotWindow";
import { MISSING_CREDIT_VIX } from "@/lib/creditVix";

export const dynamic = "force-dynamic";

export const runtime = "nodejs";


// Daily 22:25 UTC timer plus weekend slack; older means the writer is down.
const CREDIT_VIX_MAX_AGE_MS = 48 * 60 * 60_000;

async function readCreditVixFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'credit-vix' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readCreditVixFromDb,
    maxAgeMs: await snapshotMaxAgeMs(CREDIT_VIX_MAX_AGE_MS),
    label: "credit-vix",
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_CREDIT_VIX, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["credit-vix"],
  });
}
