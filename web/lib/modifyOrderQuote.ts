import type { PriceData } from "@/lib/pricesProtocol";

type ModifyQuoteOverlayInput = {
  priceData: PriceData | null;
  action: string | null | undefined;
  limitPrice: number | null | undefined;
};

function normalizeLimitPrice(limitPrice: number | null | undefined): number | null {
  if (typeof limitPrice !== "number" || !Number.isFinite(limitPrice) || limitPrice === 0) {
    return null;
  }
  return limitPrice;
}

export function applyRestingLimitToQuote({
  priceData,
  action,
  limitPrice,
}: ModifyQuoteOverlayInput): PriceData | null {
  if (!priceData) return null;

  const restingLimit = normalizeLimitPrice(limitPrice);
  if (!restingLimit) return priceData;

  const side = String(action ?? "").toUpperCase();
  if (side !== "BUY" && side !== "SELL") return priceData;

  const next: PriceData = { ...priceData };

  if (side === "SELL") {
    next.ask = next.ask != null ? Math.min(next.ask, restingLimit) : restingLimit;
    if (next.bid != null && next.ask != null && next.bid > next.ask) {
      next.bid = next.ask;
    }
    return next;
  }

  next.bid = next.bid != null ? Math.max(next.bid, restingLimit) : restingLimit;
  if (next.bid != null && next.ask != null && next.ask < next.bid) {
    next.ask = next.bid;
  }
  return next;
}
