import { NextResponse } from "next/server";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { MISSING_RECORD_HIGH_BREADTH } from "@/lib/recordHighBreadth";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";
async function fromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute({
    sql: "SELECT scan_time, payload FROM scan_snapshots WHERE service = 'record-high-breadth' ORDER BY scan_time DESC LIMIT 1", args: [],
  }, { label: "record-high-breadth" });
  if (!result.rows.length) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return { data: JSON.parse(row.payload), timestampMs: contentTimestampMs(row.scan_time) };
}
export async function GET(): Promise<Response> {
  const result = await dbFirstRead({ fromDb, maxAgeMs: 96 * 60 * 60_000, label: "record-high-breadth" });
  return NextResponse.json(result.ok ? result.data : MISSING_RECORD_HIGH_BREADTH, { headers: { "Cache-Control": "no-store" } });
}
