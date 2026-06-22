import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

/**
 * GET /api/informed-flow/[ticker]
 *
 * Proxies to FastAPI `GET /informed-flow/{ticker}` (which runs
 * `scripts/fetch_informed_flow.py` — congress + insider + institutional). On
 * an API failure, degrades to the on-disk per-ticker cache, then to a 200 +
 * `missing` empty surface. Never 4xx for a legitimate empty state.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const TICKER_RE = /^[A-Z]{1,5}$/;
const INFORMED_FLOW_DIR = join(process.cwd(), "..", "data", "informed_flow");

function normalizeTicker(raw: string): string | null {
  const upper = raw.toUpperCase();
  return TICKER_RE.test(upper) ? upper : null;
}

function cachePathFor(ticker: string): string {
  return join(INFORMED_FLOW_DIR, `${ticker}.json`);
}

function emptySurface(ticker: string): Record<string, unknown> {
  return {
    ticker,
    missing: true,
    congress_trades: [],
    insider_trades: [],
    institutional_summary: null,
  };
}

type Params = { params: Promise<{ ticker: string }> };

export async function GET(_req: Request, ctx: Params): Promise<Response> {
  const requestId = getRequestId();
  const { ticker: raw } = await ctx.params;
  const ticker = normalizeTicker(raw);
  if (!ticker) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Invalid ticker symbol" }, { status: 400 }),
      requestId,
    );
  }

  try {
    const data = await radonFetch(`/informed-flow/${ticker}`, { timeout: 65_000 });
    return setNoStoreResponseHeaders(
      NextResponse.json(data as Record<string, unknown>),
      requestId,
    );
  } catch {
    try {
      const cached = JSON.parse(await readFile(cachePathFor(ticker), "utf-8"));
      const res = NextResponse.json({ ...cached, is_stale: true });
      res.headers.set("X-Sync-Warning", "Radon API unavailable - serving cached data");
      return setNoStoreResponseHeaders(res, requestId);
    } catch {
      return setNoStoreResponseHeaders(
        NextResponse.json(emptySurface(ticker)),
        requestId,
      );
    }
  }
}
