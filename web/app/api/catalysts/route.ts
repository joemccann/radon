import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

/**
 * GET /api/catalysts
 *
 * Reads the latest catalyst feed from `data/catalysts.json` (written by
 * `scripts/fetch_catalysts.py --json`). 200 + `missing` flag when the cache
 * has not been written yet — never 4xx for a legitimate empty state.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CATALYSTS_CACHE_PATH = join(process.cwd(), "..", "data", "catalysts.json");

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const raw = await readFile(CATALYSTS_CACHE_PATH, "utf-8");
    const data = JSON.parse(raw);
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch {
    return setNoStoreResponseHeaders(
      NextResponse.json({ missing: true, scan_time: null, count: 0, catalysts: [] }),
      requestId,
    );
  }
}
