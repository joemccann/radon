import { NextResponse } from "next/server";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { fetchYahooChartQuote } from "@/lib/yahooQuote";
import type { PriceData } from "@/lib/pricesProtocol";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Delayed-quote fallback for the ES/NQ/RTY header strip. Mirrors
 * `/api/index-quote`: maps a Radon futures root to its Yahoo continuous-front
 * symbol (`=F`) and returns the same PriceData shape. Used when the realtime
 * relay is not streaming these roots (always on the isolated demo, and a
 * relay-down fallback in prod).
 */
const YAHOO_FUTURES_SYMBOLS: Record<string, string> = {
  ES: "ES=F",
  NQ: "NQ=F",
  RTY: "RTY=F",
};

async function fetchYahooFuturesQuote(symbol: string): Promise<PriceData | null> {
  const yahooSymbol = YAHOO_FUTURES_SYMBOLS[symbol];
  if (!yahooSymbol) return null;
  return fetchYahooChartQuote(symbol, yahooSymbol);
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

  if (!(symbol in YAHOO_FUTURES_SYMBOLS)) {
    return jsonApiError({
      message: "symbol is not a supported index future",
      status: 400,
      code: "BAD_REQUEST",
      requestId,
    });
  }

  try {
    const price = await fetchYahooFuturesQuote(symbol);
    const response = NextResponse.json({
      price,
      source: price ? "yahoo" : "none",
    });
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    return jsonApiError({
      message: "futures quote fallback failed",
      status: 502,
      code: "UPSTREAM_ERROR",
      detail: error instanceof Error ? error.message : String(error),
      requestId,
    });
  }
}
