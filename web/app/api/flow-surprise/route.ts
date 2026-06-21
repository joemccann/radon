import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { statSync } from "fs";
import { join } from "path";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
// Disable Next.js static caching: this handler reads live disk state
// (data/flow_surprise.json). Without this, the framework freezes the first
// response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const FLOW_SURPRISE_CACHE_PATH = join(process.cwd(), "..", "data", "flow_surprise.json");
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

interface FlowSurpriseFile {
  scan_time?: string;
  metric?: string;
  count_ok?: number;
  results?: unknown[];
  skipped?: number;
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const cache_meta = buildCacheMeta(FLOW_SURPRISE_CACHE_PATH);
  try {
    const raw = await readFile(FLOW_SURPRISE_CACHE_PATH, "utf-8");
    const data = JSON.parse(raw) as FlowSurpriseFile;
    return setNoStoreResponseHeaders(
      NextResponse.json({
        results: Array.isArray(data.results) ? data.results : [],
        metric: data.metric ?? "flow_strength",
        scan_time: data.scan_time ?? "",
        count_ok: typeof data.count_ok === "number" ? data.count_ok : 0,
        skipped: typeof data.skipped === "number" ? data.skipped : 0,
        cache_meta,
      }),
      requestId,
    );
  } catch {
    // Missing/unreadable file is a legitimate empty state, not an error.
    return setNoStoreResponseHeaders(
      NextResponse.json({
        results: [],
        missing: true,
        metric: "flow_strength",
        scan_time: "",
        count_ok: 0,
        skipped: 0,
        cache_meta,
      }),
      requestId,
    );
  }
}
