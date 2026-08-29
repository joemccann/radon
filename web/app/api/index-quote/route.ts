import { NextResponse } from "next/server";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isIndexSymbol } from "@/lib/indexSymbols";
import { fetchYahooChartQuote } from "@/lib/yahooQuote";
import type { PriceData } from "@/lib/pricesProtocol";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

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

async function fetchYahooIndexQuote(symbol: string): Promise<PriceData | null> {
  const yahooSymbol = YAHOO_INDEX_SYMBOLS[symbol];
  if (!yahooSymbol) return null;
  return fetchYahooChartQuote(symbol, yahooSymbol);
}

export const radonCapability = "read";

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
