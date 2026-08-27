import type { PriceData } from "@/lib/pricesProtocol";
import { oldestQuoteTimestamp, optionKey } from "@/lib/pricesProtocol";

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
        // R-166: unequal leg sizes are NOT a vertical. A 1x2 with the short
        // side larger carries the extra short's risk profile; with the long
        // side larger it is a backspread. Both were labelled "Bull/Bear
        // <type> Spread", which is a materially different position.
        const buyQty = Math.abs(buyLeg.quantity || 0);
        const sellQty = Math.abs(sellLeg.quantity || 0);
        if (buyQty > 0 && sellQty > 0 && buyQty !== sellQty) {
          return sellQty > buyQty ? `${type} Ratio Spread` : `${type} Backspread`;
        }
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
  if (legs.length === 3) {
    const seagull = detectRiskReversalSpread(legs);
    if (seagull) return seagull;
  }
  return `${legs.length}-Leg Combo`;
}

/**
 * Three-leg seagull: a vertical spread financed by a short option on the
 * other side. Same expiry across all legs.
 *
 *   - SELL put + bull call spread (BUY lower C, SELL higher C)
 *     → "Risk Reversal Call Spread"
 *   - SELL call + bear put spread (BUY higher P, SELL lower P)
 *     → "Risk Reversal Put Spread"
 *
 * Returns null for any other 3-leg shape (mixed expiries, long financing
 * leg, or a debit-side vertical pointing the wrong way).
 */
function detectRiskReversalSpread(legs: OrderLeg[]): string | null {
  const sameExpiry = legs.every((l) => l.expiry === legs[0].expiry);
  if (!sameExpiry) return null;

  const calls = legs.filter((l) => l.right === "C");
  const puts = legs.filter((l) => l.right === "P");

  if (calls.length === 2 && puts.length === 1) {
    const [c1, c2] = calls;
    if (puts[0].action !== "SELL" || c1.action === c2.action) return null;
    const buyCall = c1.action === "BUY" ? c1 : c2;
    const sellCall = c1.action === "SELL" ? c1 : c2;
    return buyCall.strike < sellCall.strike ? "Risk Reversal Call Spread" : null;
  }

  if (puts.length === 2 && calls.length === 1) {
    const [p1, p2] = puts;
    if (calls[0].action !== "SELL" || p1.action === p2.action) return null;
    const buyPut = p1.action === "BUY" ? p1 : p2;
    const sellPut = p1.action === "SELL" ? p1 : p2;
    return buyPut.strike > sellPut.strike ? "Risk Reversal Put Spread" : null;
  }

  return null;
}

/**
 * Detect a BEARISH risk reversal — SELL CALL + BUY PUT on the same expiry,
 * different strikes. IB Smart's combo router has been observed (2026-05-27)
 * to silently drop this structure as a BAG, even though the BULLISH
 * counterpart (BUY CALL + SELL PUT) routes fine and the individual legs as
 * singletons transmit cleanly. The chain order builder surfaces a heads-up
 * when this returns true so the operator knows to expect a possible
 * "Order stuck in PendingSubmit" error and can pre-emptively split into
 * single-leg orders. Full diagnostic in
 * `feedback_ib_combo_router_silent_drops_bearish_rr.md`.
 */
export function isBearishRiskReversal(legs: OrderLeg[]): boolean {
  if (legs.length !== 2) return false;
  const [a, b] = legs;
  if (a.expiry !== b.expiry) return false;
  if (a.right === b.right) return false;
  if (a.action === b.action) return false;
  if (a.strike === b.strike) return false; // synthetic short, not RR
  const callLeg = a.right === "C" ? a : b;
  const putLeg = a.right === "P" ? a : b;
  return callLeg.action === "SELL" && putLeg.action === "BUY";
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

export function getComboEntryAction(_legs: OrderLeg[]): "BUY" {
  // IB combo legs already encode the intended structure.
  // The BAG envelope must stay BUY for entry orders or IB reverses the leg actions.
  return "BUY";
}

export function getOrderBuilderStructureKey(legs: OrderLeg[]): string {
  return legs
    .map((leg) => [
      leg.id,
      leg.action,
      leg.right,
      leg.strike,
      leg.expiry,
      Math.max(1, Math.trunc(leg.quantity)),
    ].join(":"))
    .join("|");
}

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
  /**
   * ISO timestamp of the stalest leg quote the net was built from, or undefined
   * when any leg contributed a price with no known age (a manual leg override,
   * or a leg the relay never quoted). Feeds `comboQuotePriceData` so a combo
   * cannot render an hours-old net as a live market.
   */
  asOf?: string;
};

/**
 * Compute the marketable bid/ask/mid for a combo order.
 *
 * For a debit spread (net positive, paying to open):
 *   - BID = what you'd receive if you SELL the spread (hit bids on longs, lift asks on shorts)
 *   - ASK = what you'd pay if you BUY the spread (lift asks on longs, hit bids on shorts)
 *
 * The "natural market" perspective:
 *   - BUY leg: you pay the ASK to acquire it, receive the BID to liquidate it
 *   - SELL leg: you receive the BID to open it, pay the ASK to close it
 *
 * So for the combo as a whole:
 *   - netAsk (cost to BUY) = sum of (ask * qty) for BUY legs - sum of (bid * qty) for SELL legs
 *   - netBid (proceeds to SELL) = sum of (bid * qty) for BUY legs - sum of (ask * qty) for SELL legs
 */
export function computeNetOptionQuote(
  legs: OrderLeg[],
  prices: Record<string, PriceData>,
  ticker: string,
): NetOptionQuote {
  if (legs.length === 0) return { bid: null, ask: null, mid: null };

  // Cost to BUY the combo = pay ask on BUY legs, receive bid on SELL legs
  let netAsk = 0;
  // Proceeds to SELL the combo = receive bid on BUY legs, pay ask on SELL legs
  let netBid = 0;
  // Freshness sources, one per leg. A manually overridden leg contributes null
  // so the whole net reports an unknown age instead of a borrowed one.
  const quoteAges: Array<PriceData | null> = [];

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
    quoteAges.push(quoteSource ? pd : null);

    if (bid == null || ask == null) {
      return { bid: null, ask: null, mid: null };
    }

    if (leg.action === "BUY") {
      // BUY leg: pay ask to acquire, receive bid to liquidate
      netAsk += ask * leg.quantity;
      netBid += bid * leg.quantity;
    } else {
      // SELL leg: receive bid to open, pay ask to close
      netAsk -= bid * leg.quantity;
      netBid -= ask * leg.quantity;
    }
  }

  const bid = Math.min(netBid, netAsk);
  const ask = Math.max(netBid, netAsk);
  const mid = (bid + ask) / 2;

  return { bid, ask, mid, asOf: oldestQuoteTimestamp(quoteAges) };
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

/** Sentinel value for strikesPerSide meaning "show every strike in the chain".
 * Pass to getVisibleStrikes to bypass the ATM-centered window entirely.
 * The WS subscription is still capped at ±50 around ATM even in this mode
 * (see OptionsChainTab) to avoid flooding the relay with hundreds of ticks. */
export const ALL_STRIKES = -1;

/**
 * Return the slice of strikes to display in the options chain grid.
 *
 * @param strikes - Full sorted strike array from the chain API.
 * @param atmStrike - Nearest-ATM strike to centre the window on. Falls back
 *   to the array midpoint when null.
 * @param strikesPerSide - Number of strikes to show on each side of ATM.
 *   Pass ALL_STRIKES (-1) to return the entire array.
 */
export function getVisibleStrikes(
  strikes: number[],
  atmStrike: number | null,
  strikesPerSide: number,
): number[] {
  if (strikes.length === 0) return [];
  if (strikesPerSide === ALL_STRIKES) return strikes;
  const atmIdx = atmStrike != null ? strikes.indexOf(atmStrike) : Math.floor(strikes.length / 2);
  const startIdx = Math.max(0, atmIdx - strikesPerSide);
  const endIdx = Math.min(strikes.length, atmIdx + strikesPerSide + 1);
  return strikes.slice(startIdx, endIdx);
}
