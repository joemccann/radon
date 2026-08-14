import type { DepthBook } from "@/lib/pricesProtocol";

export type L1BboQuote = {
  symbol: string;
  kind: "stock" | "option" | "future";
  bid: number;
  ask: number;
  bidSize: number;
  askSize: number;
  timestamp?: string | null;
};

/**
 * One-level SMART/L1 book used when the relay has no entitled L2.
 * Combo legs already seed this shape; stock and single-leg option books
 * must do the same so the montage does not collapse to an empty panel.
 */
export function buildL1BboDepth(quote: L1BboQuote): DepthBook | null {
  if (!Number.isFinite(quote.bid) || !Number.isFinite(quote.ask)) return null;
  if (!Number.isFinite(quote.bidSize) || !Number.isFinite(quote.askSize)) return null;
  if (quote.bidSize <= 0 || quote.askSize <= 0) return null;
  return {
    symbol: quote.symbol,
    kind: quote.kind,
    bid: [{ price: quote.bid, size: quote.bidSize, marketMaker: null, exchange: null }],
    ask: [{ price: quote.ask, size: quote.askSize, marketMaker: null, exchange: null }],
    isSmartDepth: false,
    feed: "L1 BBO",
    entitled: true,
    timestamp: quote.timestamp || new Date().toISOString(),
  };
}
