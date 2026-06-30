import type { PriceData } from "@/lib/pricesProtocol";

/**
 * Shared Yahoo Finance v8 chart fetch + parse used by the delayed-quote
 * fallback routes (`/api/index-quote` for cash indices, `/api/futures-quote`
 * for the ES/NQ/RTY E-minis). Both map a Radon symbol to a Yahoo symbol and
 * hand it here; the parse layer is identical, so it lives once.
 */
export type YahooChartPayload = {
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

export function yahooResultToPrice(symbol: string, payload: YahooChartPayload): PriceData | null {
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

export async function fetchYahooChartQuote(symbol: string, yahooSymbol: string): Promise<PriceData | null> {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(yahooSymbol)}?range=1d&interval=5m`;
  const res = await fetch(url, {
    cache: "no-store",
    headers: { "User-Agent": "Mozilla/5.0" },
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) return null;

  const payload = await res.json() as YahooChartPayload;
  return yahooResultToPrice(symbol, payload);
}
