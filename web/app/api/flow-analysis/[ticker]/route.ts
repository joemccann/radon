import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { statSync } from "fs";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

// Disable Next.js static caching: this handler reads live disk state
// (data/flow_reports/<TICKER>.json). Without this, Next 16 freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const TICKER_RE = /^[A-Z]{1,5}$/;
const STALE_THRESHOLD_SECONDS = 600;
const FLOW_REPORTS_DIR = join(process.cwd(), "..", "data", "flow_reports");

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

function normalizeTicker(raw: string): string | null {
  const upper = raw.toUpperCase();
  return TICKER_RE.test(upper) ? upper : null;
}

function cachePathFor(ticker: string): string {
  return join(FLOW_REPORTS_DIR, `${ticker}.json`);
}

type Params = { params: Promise<{ ticker: string }> };

export const radonCapability = { GET: "read", POST: "read.spawn" };

export async function GET(_req: Request, ctx: Params): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "flow-analysis/[ticker]:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const { ticker: raw } = await ctx.params;
  const ticker = normalizeTicker(raw);
  if (!ticker) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Invalid ticker symbol" }, { status: 400 }),
      requestId,
    );
  }

  const cachePath = cachePathFor(ticker);
  try {
    const raw = await readFile(cachePath, "utf-8");
    const data = JSON.parse(raw);
    const cache_meta = buildCacheMeta(cachePath);
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...data, cache_meta }),
      requestId,
    );
  } catch {
    // No cache yet (legitimate first-time scan). Return 200 + missing:true
    // so the client can decide whether to trigger a POST without surfacing
    // a noisy 404 in the browser console / Next.js logs.
    const cache_meta = buildCacheMeta(cachePath);
    return setNoStoreResponseHeaders(
      NextResponse.json({ ticker, cache_meta, missing: true }),
      requestId,
    );
  }
}

export async function POST(_req: Request, ctx: Params): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "flow-analysis/[ticker]:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
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
    // Caddy bounds this upstream at a 30s `response_header_timeout`
    // (cloud/caddy/Caddyfile), and a 20-session AMZN pull measures 81s before
    // FastAPI has even probed the saturated general lane for a slot. A 130s
    // wait here could therefore only ever end as a raw edge 502, so answer
    // first with the cache branch below and let the scan land on its own:
    // FastAPI detaches it from this request and writes the cache when it
    // finishes, so the next load is fresh. Pinned under the edge bound by
    // `test_the_route_answers_before_the_edge_cuts_it`.
    const data = await radonFetch(`/flow-analysis/${ticker}`, {
      method: "POST",
      timeout: 25_000,
    });
    const cache_meta = buildCacheMeta(cachePathFor(ticker));
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...(data as Record<string, unknown>), cache_meta }),
      requestId,
    );
  } catch (error) {
    // Serve cached data on failure so the UI degrades gracefully.
    try {
      const raw = await readFile(cachePathFor(ticker), "utf-8");
      const cached = JSON.parse(raw);
      const cache_meta = buildCacheMeta(cachePathFor(ticker));
      const res = NextResponse.json({ ...cached, cache_meta, is_stale: true });
      res.headers.set("X-Sync-Warning", "Radon API unavailable - serving cached data");
      return setNoStoreResponseHeaders(res, requestId);
    } catch {
      const message = error instanceof Error ? error.message : "Flow report failed";
      return setNoStoreResponseHeaders(
        NextResponse.json({ error: message }, { status: 502 }),
        requestId,
      );
    }
  }
}
