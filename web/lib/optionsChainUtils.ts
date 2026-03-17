import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";

/* ─── Types ─── */

export type OrderLeg = {
  id: string;
  action: "BUY" | "SELL";
  right: "C" | "P";
  strike: number;
  expiry: string;
  quantity: number;
  limitPrice: number | null;
  priceManuallySet?: boolean;
};

/* ─── Expiry formatting ─── */

export function formatExpiry(expiry: string): string {
  if (expiry.length !== 8) return expiry;
  const y = expiry.slice(0, 4);
  const m = expiry.slice(4, 6);
  const d = expiry.slice(6, 8);
  return `${y}-${m}-${d}`;
}

export function daysToExpiry(expiry: string): number {
  if (expiry.length !== 8) return 0;
  const y = parseInt(expiry.slice(0, 4), 10);
  const m = parseInt(expiry.slice(4, 6), 10) - 1;
  const d = parseInt(expiry.slice(6, 8), 10);
  const target = new Date(y, m, d);
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.ceil((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

/* ─── Structure detection ─── */

export function detectStructure(legs: OrderLeg[]): string {
  if (legs.length === 0) return "";
  if (legs.length === 1) {
    const l = legs[0];
    return `${l.action === "BUY" ? "Long" : "Short"} ${l.right === "C" ? "Call" : "Put"}`;
  }
  if (legs.length === 2) {
    const [a, b] = legs;
    const sameExpiry = a.expiry === b.expiry;
    if (!sameExpiry) return "Calendar Spread";

    // Both calls or both puts
    if (a.right === b.right) {
      const hasBuy = a.action !== b.action;
      if (hasBuy) {
        const type = a.right === "C" ? "Call" : "Put";
        const buyLeg = a.action === "BUY" ? a : b;
        const sellLeg = a.action === "SELL" ? a : b;
        if (a.right === "C") {
          return buyLeg.strike < sellLeg.strike ? `Bull ${type} Spread` : `Bear ${type} Spread`;
        }
        return buyLeg.strike > sellLeg.strike ? `Bear ${type} Spread` : `Bull ${type} Spread`;
      }
    }

    // Call + Put, opposite actions
    if (a.right !== b.right && a.action !== b.action) {
      if (a.strike === b.strike) return "Synthetic";
      return "Risk Reversal";
    }

    // Same action, call + put
    if (a.right !== b.right && a.action === b.action) {
      if (a.strike === b.strike) return a.action === "BUY" ? "Long Straddle" : "Short Straddle";
      return a.action === "BUY" ? "Long Strangle" : "Short Strangle";
    }
  }
  return `${legs.length}-Leg Combo`;
}

function greatestCommonDivisor(a: number, b: number): number {
  let x = Math.abs(Math.trunc(a)) || 1;
  let y = Math.abs(Math.trunc(b)) || 1;
  while (y !== 0) {
    const remainder = x % y;
    x = y;
    y = remainder;
  }
  return x || 1;
}

export type NormalizedComboOrder = {
  quantity: number;
  legs: OrderLeg[];
};

export function normalizeComboOrder(legs: OrderLeg[]): NormalizedComboOrder {
  if (legs.length === 0) return { quantity: 1, legs: [] };

  const quantities = legs.map((leg) => Math.max(1, Math.trunc(leg.quantity)));
  const quantity = quantities.reduce((acc, value) => greatestCommonDivisor(acc, value));

  return {
    quantity,
    legs: legs.map((leg, index) => ({
      ...leg,
      quantity: quantities[index] / quantity,
    })),
  };
}

/* ─── Net price calculation ─── */

export function computeNetPrice(legs: OrderLeg[], prices: Record<string, PriceData>): number | null {
  let net = 0;
  for (const leg of legs) {
    const key = optionKey({
      symbol: leg.id.split("_")[0],
      expiry: leg.expiry,
      strike: leg.strike,
      right: leg.right,
    });
    const pd = prices[key];
    const useManualPrice = leg.priceManuallySet === true;
    const mid = !useManualPrice && pd?.bid != null && pd?.ask != null
      ? (pd.bid + pd.ask) / 2
      : leg.limitPrice;
    if (mid == null) return null;
    const sign = leg.action === "BUY" ? 1 : -1;
    net += sign * mid * leg.quantity;
  }
  return net;
}

export type NetOptionQuote = {
  bid: number | null;
  ask: number | null;
  mid: number | null;
};

export function computeNetOptionQuote(
  legs: OrderLeg[],
  prices: Record<string, PriceData>,
  ticker: string,
): NetOptionQuote {
  if (legs.length === 0) return { bid: null, ask: null, mid: null };

  let netBid = 0;
  let netAsk = 0;
  for (const leg of legs) {
    const key = optionKey({
      symbol: ticker,
      expiry: leg.expiry,
      strike: leg.strike,
      right: leg.right,
    });
    const pd = prices[key];

    // Prefer live combo quote when available unless user explicitly overrides
    // leg-level price in the builder.
    const quoteSource = pd && !leg.priceManuallySet;
    const bid = quoteSource ? pd?.bid : leg.limitPrice;
    const ask = quoteSource ? pd?.ask : leg.limitPrice;

    if (bid == null || ask == null) {
      return { bid: null, ask: null, mid: null };
    }

    const sign = leg.action === "BUY" ? 1 : -1;
    netBid += sign * bid * leg.quantity;
    netAsk += sign * ask * leg.quantity;
  }

  const absBid = Math.abs(netBid);
  const absAsk = Math.abs(netAsk);
  const bid = Math.min(absBid, absAsk);
  const ask = Math.max(absBid, absAsk);
  const mid = (bid + ask) / 2;

  return { bid, ask, mid };
}

/* ─── ATM strike finder ─── */

export function findAtmStrike(strikes: number[], currentPrice: number): number | null {
  if (strikes.length === 0) return null;
  let closest = strikes[0];
  let minDiff = Math.abs(strikes[0] - currentPrice);
  for (const s of strikes) {
    const diff = Math.abs(s - currentPrice);
    if (diff < minDiff) {
      minDiff = diff;
      closest = s;
    }
  }
  return closest;
}

/* ─── Visible strikes around ATM ─── */

export function getVisibleStrikes(
  strikes: number[],
  atmStrike: number | null,
  strikesPerSide: number,
): number[] {
  if (strikes.length === 0) return [];
  const atmIdx = atmStrike != null ? strikes.indexOf(atmStrike) : Math.floor(strikes.length / 2);
  const startIdx = Math.max(0, atmIdx - strikesPerSide);
  const endIdx = Math.min(strikes.length, atmIdx + strikesPerSide + 1);
  return strikes.slice(startIdx, endIdx);
}
