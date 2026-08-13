import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";

// Live-state route: force-dynamic per the cache contract.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

/**
 * Intraday 10Y-3M Treasury spread ESTIMATE for the CURVE tab.
 *
 * Why not the headline 10Y-2Y, and why Yahoo (probed live 2026-08-03,
 * docs/indicators/curve.md): IB has no live CMT legs on this account (CME
 * micro yield futures 2YY/10Y are delisted; TNX needs a CBOE index
 * subscription), UW has no rates endpoints, and no source in the ladder
 * quotes a live 2Y. ^TNX (10Y) and ^IRX (13-week bill) are live from the
 * same source with the same delay, so the estimate involves no modeling.
 * Contract: an unavailable estimate is HTTP 200 with missing:true.
 */
const MISSING_LIVE = {
  missing: true,
  y10: null,
  y3m: null,
  spread_10y_3m: null,
  asof: null,
  source: null,
};

const YAHOO_CHART_URL = (symbol: string) =>
  `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1d&interval=5m`;

// Plain-yield sanity bounds: a Yahoo scale change (^TNX historically quoted
// 10x the yield) must read as missing, never as a 46.9% yield.
const YIELD_MIN_PCT = 0.0;
const YIELD_MAX_PCT = 20.0;

const CACHE_TTL_MS = 60_000;
const NEGATIVE_TTL_MS = 5_000;
const PROVIDER_TIMEOUT_MS = 3_000;

interface LegQuote {
  price: number;
  timeMs: number;
}

let cached: { payload: Record<string, unknown>; at: number } | null = null;
let refreshInFlight: Promise<Record<string, unknown>> | null = null;
let negativeUntil = 0;

async function fetchLegQuote(symbol: string): Promise<LegQuote | null> {
  const res = await fetch(YAHOO_CHART_URL(symbol), {
    headers: { "User-Agent": "Mozilla/5.0" },
    cache: "no-store",
    signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS),
  });
  if (!res.ok) return null;
  const body = (await res.json()) as {
    chart?: { result?: Array<{ meta?: { regularMarketPrice?: number; regularMarketTime?: number } }> };
  };
  const meta = body.chart?.result?.[0]?.meta;
  const price = meta?.regularMarketPrice;
  const time = meta?.regularMarketTime;
  if (typeof price !== "number" || typeof time !== "number") return null;
  if (!Number.isFinite(price) || price < YIELD_MIN_PCT || price > YIELD_MAX_PCT) return null;
  return { price, timeMs: time * 1000 };
}

async function buildLivePayload(): Promise<Record<string, unknown>> {
  const [y10, y3m] = await Promise.all([
    fetchLegQuote("^TNX").catch(() => null),
    fetchLegQuote("^IRX").catch(() => null),
  ]);
  if (!y10 || !y3m) return MISSING_LIVE;
  return {
    y10: y10.price,
    y3m: y3m.price,
    spread_10y_3m: y10.price - y3m.price,
    // The estimate is only as fresh as its stalest input.
    asof: new Date(Math.min(y10.timeMs, y3m.timeMs)).toISOString(),
    source: "yahoo",
  };
}

async function refreshLivePayload(): Promise<Record<string, unknown>> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = buildLivePayload().then((payload) => {
    if (payload.missing) {
      negativeUntil = Date.now() + NEGATIVE_TTL_MS;
      return cached?.payload ?? MISSING_LIVE;
    }
    cached = { payload, at: Date.now() };
    negativeUntil = 0;
    return payload;
  }).finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

export async function GET() {
  const requestId = getRequestId();
  if (!cached || Date.now() - cached.at >= CACHE_TTL_MS) {
    const payload = Date.now() < negativeUntil
      ? (cached?.payload ?? MISSING_LIVE)
      : await refreshLivePayload();
    if (payload.missing || !cached) {
      const response = NextResponse.json(payload);
      setCacheResponseHeaders(response, {
        maxAgeSeconds: 30,
        staleWhileRevalidateSeconds: 120,
        requestId,
        cacheState: "MISS",
        tags: ["yield-curve"],
      });
      return response;
    }
  }
  const response = NextResponse.json(cached.payload);
  setCacheResponseHeaders(response, {
    maxAgeSeconds: 30,
    staleWhileRevalidateSeconds: 120,
    requestId,
    cacheState: "HIT",
    tags: ["yield-curve"],
  });
  return response;
}
