import { NextResponse } from "next/server";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isIndexSymbol } from "@/lib/indexSymbols";
import type { PriceData } from "@/lib/pricesProtocol";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type YahooIndexPayload = {
  chart?: {
    result?: Array<{
      meta?: Record<string, unknown>;
      timestamp?: unknown;
      indicators?: {
        quote?: Array<Record<string, unknown>>;
      };
    }>;
  };
};

const YAHOO_INDEX_SYMBOLS: Record<string, string> = {
  VIX: "^VIX",
  VXN: "^VXN",
  VVIX: "^VVIX",
  SPX: "^GSPC",
  NDX: "^NDX",
  RUT: "^RUT",
  DJX: "^DJI",
  OEX: "^OEX",
  XSP: "^XSP",
};

function finitePositive(value: unknown): number | null {
  const n = typeof value === "number" ? value : Number.parseFloat(String(value ?? ""));
  return Number.isFinite(n) && n > 0 ? n : null;
}

function latestPositive(values: unknown): number | null {
  if (!Array.isArray(values)) return null;
  for (let i = values.length - 1; i >= 0; i--) {
    const n = finitePositive(values[i]);
    if (n != null) return n;
  }
  return null;
}

function latestTimestamp(timestamps: unknown): string {
  if (!Array.isArray(timestamps)) return new Date().toISOString();
  for (let i = timestamps.length - 1; i >= 0; i--) {
    const seconds = finitePositive(timestamps[i]);
    if (seconds != null) return new Date(seconds * 1000).toISOString();
  }
  return new Date().toISOString();
}

function yahooResultToPrice(symbol: string, payload: YahooIndexPayload): PriceData | null {
  const result = payload.chart?.result?.[0];
  if (!result) return null;

  const meta = result.meta ?? {};
  const quote = result.indicators?.quote?.[0] ?? {};
  const last =
    finitePositive(meta.regularMarketPrice) ??
    latestPositive(quote.close) ??
    finitePositive(meta.previousClose) ??
    finitePositive(meta.regularMarketPreviousClose) ??
    finitePositive(meta.chartPreviousClose);
  if (last == null) return null;

  const previousClose =
    finitePositive(meta.previousClose) ??
    finitePositive(meta.regularMarketPreviousClose) ??
    finitePositive(meta.chartPreviousClose);

  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: finitePositive(meta.regularMarketVolume) ?? latestPositive(quote.volume),
    high: finitePositive(meta.regularMarketDayHigh) ?? latestPositive(quote.high),
    low: finitePositive(meta.regularMarketDayLow) ?? latestPositive(quote.low),
    open: finitePositive(meta.regularMarketOpen) ?? latestPositive(quote.open),
    close: previousClose,
    week52High: finitePositive(meta.fiftyTwoWeekHigh),
    week52Low: finitePositive(meta.fiftyTwoWeekLow),
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    fwd: null,
    fwdCurve: null,
    timestamp: typeof meta.regularMarketTime === "number"
      ? new Date(meta.regularMarketTime * 1000).toISOString()
      : latestTimestamp(result.timestamp),
  };
}

async function fetchYahooIndexQuote(symbol: string): Promise<PriceData | null> {
  const yahooSymbol = YAHOO_INDEX_SYMBOLS[symbol];
  if (!yahooSymbol) return null;

  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(yahooSymbol)}?range=1d&interval=5m`;
  const res = await fetch(url, {
    cache: "no-store",
    headers: { "User-Agent": "Mozilla/5.0" },
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) return null;

  const payload = await res.json() as YahooIndexPayload;
  return yahooResultToPrice(symbol, payload);
}

export async function GET(request: Request): Promise<Response> {
  const requestId = getRequestId();
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol")?.trim().toUpperCase() ?? "";

  if (!symbol) {
    return jsonApiError({
      message: "symbol parameter required",
      status: 400,
      code: "BAD_REQUEST",
      requestId,
    });
  }

  if (!isIndexSymbol(symbol)) {
    return jsonApiError({
      message: "symbol is not a supported index",
      status: 400,
      code: "BAD_REQUEST",
      requestId,
    });
  }

  try {
    const price = await fetchYahooIndexQuote(symbol);
    const response = NextResponse.json({
      price,
      source: price ? "yahoo" : "none",
    });
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    return jsonApiError({
      message: "index quote fallback failed",
      status: 502,
      code: "UPSTREAM_ERROR",
      detail: error instanceof Error ? error.message : String(error),
      requestId,
    });
  }
}
