import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { statSync } from "fs";
import { join } from "path";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "theta_harvester.json");
const STALE_THRESHOLD_SECONDS = 6 * 60 * 60;

type CacheMeta = {
  last_refresh: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_threshold_seconds: number;
};

function buildCacheMeta(filePath: string): CacheMeta {
  try {
    const s = statSync(filePath);
    const ageSeconds = (Date.now() - s.mtime.getTime()) / 1000;
    return {
      last_refresh: s.mtime.toISOString(),
      age_seconds: Math.round(ageSeconds),
      is_stale: ageSeconds > STALE_THRESHOLD_SECONDS,
      stale_threshold_seconds: STALE_THRESHOLD_SECONDS,
    };
  } catch {
    return {
      last_refresh: null,
      age_seconds: null,
      is_stale: true,
      stale_threshold_seconds: STALE_THRESHOLD_SECONDS,
    };
  }
}

export function emptyThetaHarvesterPayload() {
  return {
    scan_time: "",
    source: "Unusual Whales",
    universe: "preset:ndx100",
    requested_tickers: [],
    tickers_scanned: 0,
    candidates_found: 0,
    theta_harvest_count: 0,
    results: [],
  };
}

export async function readThetaHarvesterCache(): Promise<Record<string, unknown> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  return JSON.parse(raw) as Record<string, unknown>;
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const cache_meta = buildCacheMeta(CACHE_PATH);
  try {
    const data = await readThetaHarvesterCache();
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...data, cache_meta }),
      requestId,
    );
  } catch {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...emptyThetaHarvesterPayload(), cache_meta }),
      requestId,
    );
  }
}
