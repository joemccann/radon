import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { statSync } from "fs";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "scanner.json");
const STALE_THRESHOLD_SECONDS = 600;

interface CacheMeta {
  last_refresh: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_threshold_seconds: number;
}

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

export async function GET(): Promise<Response> {
  try {
    const raw = await readFile(CACHE_PATH, "utf-8");
    const data = JSON.parse(raw);
    const cache_meta = buildCacheMeta(CACHE_PATH);
    return NextResponse.json({ ...data, cache_meta });
  } catch {
    const cache_meta = buildCacheMeta(CACHE_PATH);
    return NextResponse.json({
      scan_time: "",
      tickers_scanned: 0,
      signals_found: 0,
      top_signals: [],
      cache_meta,
    });
  }
}

export async function POST(): Promise<Response> {
  try {
    const data = await radonFetch("/scan", { method: "POST", timeout: 130_000 });
    const cache_meta = buildCacheMeta(CACHE_PATH);
    return NextResponse.json({ ...data, cache_meta });
  } catch (error) {
    // Serve cached data on failure
    try {
      const raw = await readFile(CACHE_PATH, "utf-8");
      const cached = JSON.parse(raw);
      const cache_meta = buildCacheMeta(CACHE_PATH);
      const res = NextResponse.json({ ...cached, cache_meta, is_stale: true });
      res.headers.set("X-Sync-Warning", "Radon API unavailable - serving cached data");
      return res;
    } catch {
      const message = error instanceof Error ? error.message : "Scanner failed";
      return NextResponse.json({ error: message }, { status: 502 });
    }
  }
}
