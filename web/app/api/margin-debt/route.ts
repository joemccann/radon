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

const CACHE_PATH = join(process.cwd(), "..", "data", "margin_debt.json");

// Contract: absent margin-debt data is HTTP 200 with missing:true, never a 4xx.
const MISSING_MARGIN_DEBT = {
  missing: true,
  scan_time: null,
  count: 0,
  series: [],
  current: null,
  splice: null,
  normalization: null,
};

// The refresh timer runs daily (monthly source, conditional GET no-ops the
// unchanged days), so a snapshot older than two days means the writer is down.
const MARGIN_DEBT_MAX_AGE_MS = 48 * 60 * 60_000;

async function readMarginDebtFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'margin-debt' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readMarginDebtFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readMarginDebtFromDb,
    fromDisk: readMarginDebtFromDisk,
    maxAgeMs: MARGIN_DEBT_MAX_AGE_MS,
    label: "margin-debt",
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_MARGIN_DEBT);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["margin-debt"],
  });
}
