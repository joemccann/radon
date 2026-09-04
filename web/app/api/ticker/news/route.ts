import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import {
  getRequestId,
  jsonApiError,
  setCacheResponseHeaders,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { boundedPositiveInt, boundedTicker } from "@/lib/requestBounds";
import { countedUwFetch } from "@/lib/uwCountedFetch";

export const runtime = "nodejs";

const NEWS_CACHE_SECONDS = 45;
const PROVIDER_TIMEOUT_MS = 3_000;

/** Try UW headlines first, fall back to Yahoo Finance on any error. */
export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "ticker/news:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const { searchParams } = new URL(request.url);
  const ticker = boundedTicker(searchParams.get("ticker"));
  const limit = boundedPositiveInt(searchParams.get("limit"), 20, 50);

  if (!ticker || limit === null) {
    return jsonApiError({
      message: "valid ticker and limit between 1 and 50 required",
      status: 400,
      code: "BAD_REQUEST",
      requestId,
    });
  }

  const symbol = ticker;

  // Source 1: Unusual Whales
  const uwResult = await fetchUW(symbol, limit, requestId);
  if (uwResult) {
    const response = NextResponse.json({ ...uwResult, requestId });
    return setCacheResponseHeaders(response, {
      maxAgeSeconds: NEWS_CACHE_SECONDS,
      staleWhileRevalidateSeconds: 120,
      requestId,
      cacheState: "HIT",
      tags: [`ticker-news.${symbol}`],
    });
  }

  // Source 2: Yahoo Finance (fallback)
  const yahooResult = await fetchYahoo(symbol, limit, requestId);
  if (yahooResult) {
    const response = NextResponse.json({ ...yahooResult, requestId });
    return setCacheResponseHeaders(response, {
      maxAgeSeconds: NEWS_CACHE_SECONDS,
      staleWhileRevalidateSeconds: 120,
      requestId,
      cacheState: "MISS",
      tags: [`ticker-news.${symbol}`],
    });
  }

  // All sources failed
  const response = NextResponse.json(
    {
      data: [],
      source: "none",
      error: "All news sources unavailable",
      requestId,
    },
  );
  return setNoStoreResponseHeaders(response, requestId);
}

async function fetchUW(
  ticker: string,
  limit: number,
  requestId: string,
): Promise<{ data: unknown[]; source: string } | null> {
  const token = process.env.UW_TOKEN;
  if (!token) return null;

  try {
    const url = new URL("https://api.unusualwhales.com/api/news/headlines");
    url.searchParams.set("ticker", ticker);
    url.searchParams.set("limit", String(limit));

    const res = await countedUwFetch(url.toString(), {
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS),
    });

    if (!res.ok) {
      console.warn("[ticker/news] UW failed", {
        ticker,
        requestId,
        status: res.status,
      });
      return null;
    }

    const json = await res.json();
    const items = json?.data ?? json ?? [];
    if (!Array.isArray(items) || items.length === 0) return null;

    return { data: items, source: "unusualwhales" };
  } catch {
    console.warn("[ticker/news] UW fetch failed", { ticker, requestId });
    return null;
  }
}

async function fetchYahoo(
  ticker: string,
  limit: number,
  requestId: string,
): Promise<{ data: unknown[]; source: string } | null> {
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?modules=news`;
    const res = await fetch(url, {
      cache: "no-store",
      headers: {
        "User-Agent": "Mozilla/5.0",
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS),
    });

    // Yahoo chart endpoint doesn't include news — try the search endpoint
    if (!res.ok) {
      return fetchYahooSearch(ticker, limit, requestId);
    }

    // If chart worked but no news, try search
    return fetchYahooSearch(ticker, limit, requestId);
  } catch {
    return fetchYahooSearch(ticker, limit, requestId);
  }
}

async function fetchYahooSearch(
  ticker: string,
  limit: number,
  requestId: string,
): Promise<{ data: unknown[]; source: string } | null> {
  try {
    const res = await fetch(
      `https://query2.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(ticker)}&newsCount=${limit}&quotesCount=0`,
      {
        cache: "no-store",
        headers: {
          "User-Agent": "Mozilla/5.0",
          Accept: "application/json",
        },
        signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS),
      },
    );

    if (!res.ok) return null;

    const json = await res.json();
    const news = json?.news;
    if (!Array.isArray(news) || news.length === 0) return null;

    const items = news.slice(0, limit).map((n: Record<string, unknown>) => ({
      headline: n.title ?? "",
      source: (n.publisher as string) ?? "",
      created_at: typeof n.providerPublishTime === "number"
        ? new Date(n.providerPublishTime * 1000).toISOString()
        : "",
      url: (n.link as string) ?? "",
      is_major: false,
      tickers: [ticker],
      requestId,
    }));

    return { data: items, source: "yahoo" };
  } catch {
    console.warn("[ticker/news] Yahoo fetch failed", { ticker, requestId });
    return null;
  }
}
