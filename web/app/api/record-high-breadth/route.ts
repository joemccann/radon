import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { MISSING_RECORD_HIGH_BREADTH } from "@/lib/recordHighBreadth";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";
const CACHE_PATH = join(process.cwd(), "..", "data", "record_high_breadth.json");
async function fromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute({
    sql: "SELECT scan_time, payload FROM scan_snapshots WHERE service = 'record-high-breadth' ORDER BY scan_time DESC LIMIT 1", args: [],
  }, { label: "record-high-breadth" });
  if (!result.rows.length) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return { data: JSON.parse(row.payload), timestampMs: contentTimestampMs(row.scan_time) };
}
async function fromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const data = JSON.parse(await readFile(CACHE_PATH, "utf-8")) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}
export async function GET(): Promise<Response> {
  const result = await dbFirstRead({ fromDb, fromDisk, maxAgeMs: 96 * 60 * 60_000, label: "record-high-breadth" });
  return NextResponse.json(result.ok ? result.data : MISSING_RECORD_HIGH_BREADTH, { headers: { "Cache-Control": "no-store" } });
}
