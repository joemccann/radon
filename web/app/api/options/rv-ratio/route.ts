import { readFile } from "fs/promises";
import { join } from "path";

import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { radonFetch } from "@/lib/radonApi";
import { isSnapshotCurrent } from "@/lib/rvRatio";
import {
  RV_RATIO_SCAN_TIMEOUT_MS,
  optionsErrorResponse,
  optionsJson,
} from "../_shared";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;

// Loose wall-clock budget purely to survive long weekends; the meaningful
// freshness flag is the session-relative `stale` computed below.
const RV_RATIO_MAX_AGE_MS = 4 * 24 * 3_600_000;

type SnapshotPayload = Record<string, unknown>;

type ParsedSymbol =
  | { symbol: string; response?: undefined }
  | { symbol?: undefined; response: Response };

function parseSymbol(request: Request): ParsedSymbol {
  const raw = new URL(request.url).searchParams.get("symbol");
  if (!raw) {
    return { response: optionsJson({ error: "Required: symbol", code: "BAD_REQUEST" }, 400) };
  }
  const symbol = raw.toUpperCase();
  if (!SYMBOL_RE.test(symbol)) {
    return { response: optionsJson({ error: "Invalid symbol", code: "BAD_REQUEST" }, 400) };
  }
  return { symbol };
}

async function readRvRatioFromDb(
  symbol: string,
): Promise<TimestampedRead<SnapshotPayload> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT taken_at, payload FROM rv_ratio_snapshots WHERE symbol = ?`,
    args: [symbol],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { taken_at: string; payload: string };
  return {
    data: JSON.parse(row.payload) as SnapshotPayload,
    timestampMs: contentTimestampMs(row.taken_at),
  };
}

async function readRvRatioFromDisk(
  symbol: string,
): Promise<TimestampedRead<SnapshotPayload> | null> {
  const cachePath = join(process.cwd(), "..", "data", "rv_ratio", `${symbol}.json`);
  const raw = await readFile(cachePath, "utf-8");
  const data = JSON.parse(raw) as SnapshotPayload;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

function snapshotLastDate(payload: SnapshotPayload): string | null {
  const coverage = payload.coverage;
  if (!coverage || typeof coverage !== "object") return null;
  const lastDate = (coverage as SnapshotPayload).last_date;
  return typeof lastDate === "string" ? lastDate : null;
}

function withSessionStaleness(payload: SnapshotPayload): SnapshotPayload {
  return { ...payload, stale: !isSnapshotCurrent(snapshotLastDate(payload)) };
}

export async function GET(request: Request): Promise<Response> {
  const parsed = parseSymbol(request);
  if (parsed.response) return parsed.response;
  const { symbol } = parsed;

  const result = await dbFirstRead({
    fromDb: () => readRvRatioFromDb(symbol),
    fromDisk: () => readRvRatioFromDisk(symbol),
    maxAgeMs: RV_RATIO_MAX_AGE_MS,
    label: "rv-ratio",
  });
  if (!result.ok) return optionsJson({ schema_version: 1, symbol, missing: true });
  return optionsJson(withSessionStaleness(result.data));
}

export async function POST(request: Request): Promise<Response> {
  const parsed = parseSymbol(request);
  if (parsed.response) return parsed.response;
  const { symbol } = parsed;

  try {
    const data = await radonFetch<SnapshotPayload>(`/options/rv-ratio/${symbol}/scan`, {
      method: "POST",
      timeout: RV_RATIO_SCAN_TIMEOUT_MS,
    });
    // Cooldown / missing variants carry no coverage; pass them through untouched.
    return optionsJson(snapshotLastDate(data) ? withSessionStaleness(data) : data);
  } catch (error) {
    return optionsErrorResponse("RV ratio scan unavailable", error);
  }
}
