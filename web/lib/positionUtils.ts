import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";

/* ─── Formatters ──────────────────────────────────────────── */

export const fmtUsd = (n: number) => `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
export const fmtPrice = (n: number) => `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const fmtPriceOrCalculated = (n: number, isCalculated: boolean) => isCalculated ? `C${fmtPrice(n)}` : fmtPrice(n);

type ResolvedRealtimePrice = {
  price: number | null;
  isCalculated: boolean;
};

function isPositiveNumber(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value) && value > 0;
}

export function resolveRealtimePrice(
  priceData?: PriceData | null,
  fallbackPrice?: number | null,
  fallbackIsCalculated = false,
): ResolvedRealtimePrice {
  const last = isPositiveNumber(priceData?.last) ? priceData.last : null;
  const bid = isPositiveNumber(priceData?.bid) ? priceData.bid : null;
  const ask = isPositiveNumber(priceData?.ask) ? priceData.ask : null;

  if (last != null) {
    // For options (symbol contains "_"): when last is outside the bid-ask
    // spread, the last trade is stale and the live bid/ask better reflects
    // current value. Use mid instead. This catches cases like IWM weeklies
    // where last=3.24 but bid=3.49/ask=3.53 — displaying 3.24 is misleading.
    if (priceData?.symbol?.includes("_") && bid != null && ask != null) {
      const lo = Math.min(bid, ask);
      const hi = Math.max(bid, ask);
      if (last < lo || last > hi) {
        const mid = Number(((bid + ask) / 2).toFixed(4));
        return { price: mid, isCalculated: true };
      }
    }
    return { price: last, isCalculated: Boolean(priceData?.lastIsCalculated) };
  }

  if (bid != null && ask != null) {
    return { price: Number(((bid + ask) / 2).toFixed(4)), isCalculated: true };
  }

  if (isPositiveNumber(fallbackPrice)) {
    return { price: fallbackPrice, isCalculated: fallbackIsCalculated };
  }

  return { price: null, isCalculated: false };
}

/* ─── Position math ───────────────────────────────────────── */

export function resolveMarketValue(pos: PortfolioPosition): number | null {
  // For multi-leg positions, always recompute sign-aware from legs
  if (pos.legs.length > 1) {
    const known = pos.legs.filter((l) => l.market_value != null);
    if (known.length === 0) return null;
    return known.reduce((s, l) => {
      const sign = l.direction === "LONG" ? 1 : -1;
      return s + sign * Math.abs(l.market_value!);
    }, 0);
  }
  if (pos.market_value != null) return pos.market_value;
  const single = pos.legs[0];
  return single?.market_value ?? null;
}

export function getMultiplier(pos: PortfolioPosition): number {
  return pos.structure_type === "Stock" ? 1 : 100;
}

export function resolveEntryCost(pos: PortfolioPosition): number {
  if (pos.legs.length > 1) {
    return pos.legs.reduce((s, l) => {
      const sign = l.direction === "LONG" ? 1 : -1;
      return s + sign * Math.abs(l.entry_cost);
    }, 0);
  }
  return pos.entry_cost;
}

export function getAvgEntry(pos: PortfolioPosition): number {
  const mult = getMultiplier(pos);
  return resolveEntryCost(pos) / (pos.contracts * mult);
}

export function getLastPrice(pos: PortfolioPosition): number | null {
  const mv = resolveMarketValue(pos);
  if (mv == null) return null;
  const mult = getMultiplier(pos);
  return mv / (pos.contracts * mult);
}

export function getLastPriceIsCalculated(pos: PortfolioPosition): boolean {
  if (pos.market_price_is_calculated != null) return pos.market_price_is_calculated;
  if (pos.legs.length === 1) {
    return Boolean(pos.legs[0]?.market_price_is_calculated);
  }
  return pos.legs.some((leg) => Boolean(leg.market_price_is_calculated));
}

/* ─── Price key resolution ────────────────────────────────── */

/**
 * Build a composite price key for a leg within a position.
 * Returns null for Stock legs or missing data.
 */
export function legPriceKey(
  ticker: string,
  expiry: string,
  leg: { type: string; strike: number | null },
): string | null {
  if (leg.type === "Stock") return null;
  if (leg.strike == null || leg.strike === 0) return null;
  if (!expiry || expiry === "N/A") return null;
  const right = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : null;
  if (!right) return null;
  const expiryClean = expiry.replace(/-/g, "");
  if (expiryClean.length !== 8) return null;
  return optionKey({ symbol: ticker.toUpperCase(), expiry: expiryClean, strike: leg.strike, right });
}

/* ─── Spread net price resolution ─────────────────────────── */

/**
 * Compute synthetic PriceData for a multi-leg spread from per-leg WS prices.
 * Returns null for single-leg, stock positions, or when leg prices are unavailable.
 */
export function resolveSpreadPriceData(
  ticker: string,
  position: PortfolioPosition,
  prices: Record<string, PriceData>,
): PriceData | null {
  if (position.structure_type === "Stock") return null;
  if (position.legs.length < 2) return null;

  let netBid = 0;
  let netAsk = 0;
  for (const leg of position.legs) {
    const key = legPriceKey(ticker, position.expiry, leg);
    if (!key) return null;
    const lp = prices[key];
    if (!lp || lp.bid == null || lp.ask == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    netBid += sign * lp.bid;
    netAsk += sign * lp.ask;
  }

  const lo = Math.round(Math.min(netBid, netAsk) * 100) / 100;
  const hi = Math.round(Math.max(netBid, netAsk) * 100) / 100;
  const mid = Number((((lo + hi) / 2)).toFixed(2));

  return {
    symbol: ticker,
    last: mid,
    lastIsCalculated: true,
    bid: lo,
    ask: hi,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  };
}

/* ─── Same-day position detection ─────────────────────────── */

/** Return today's date in ET (YYYY-MM-DD). */
function todayInET(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** True when the position was opened today. Yesterday's close is
 *  meaningless as a baseline because the position didn't exist. */
function isSameDay(pos: PortfolioPosition): boolean {
  return pos.entry_date === todayInET();
}

/** Compute real-time market value from WS prices for option positions. */
function computeRtMv(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock" || !prices) return null;
  let rtMv = 0;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    const current = resolveRealtimePrice(lp, leg.market_price, Boolean(leg.market_price_is_calculated)).price;
    if (current == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    rtMv += sign * current * leg.contracts * 100;
  }
  return rtMv;
}

/* ─── Option daily change ─────────────────────────────────── */

export function getOptionDailyChg(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock" || !prices) return null;

  // Same-day position: the position didn't exist yesterday, so yesterday's
  // close is meaningless. Use entry cost as both baseline and denominator.
  // This applies whether or not IB daily P&L is available — for % calc the
  // denominator must reflect the position's actual starting value (entry cost).
  if (isSameDay(pos)) {
    const ec = resolveEntryCost(pos);
    if (ec === 0) return null;
    if (pos.ib_daily_pnl != null) {
      return (pos.ib_daily_pnl / Math.abs(ec)) * 100;
    }
    const rtMv = computeRtMv(pos, prices);
    const mv = rtMv ?? resolveMarketValue(pos);
    if (mv == null) return null;
    return ((mv - ec) / Math.abs(ec)) * 100;
  }

  // Compute WS close-based daily P&L and close value (needed for % calc)
  let wsDailyPnl = 0;
  let closeValue = 0;
  let hasClose = false;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    const current = resolveRealtimePrice(lp, leg.market_price, Boolean(leg.market_price_is_calculated)).price;
    if (current == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    if (lp?.close != null && lp.close > 0) {
      wsDailyPnl += sign * (current - lp.close) * leg.contracts * 100;
      closeValue += sign * lp.close * leg.contracts * 100;
      hasClose = true;
    }
  }
  if (!hasClose || closeValue === 0) return null;

  // Prefer IB's per-position daily P&L (handles intraday additions correctly)
  const effectivePnl = pos.ib_daily_pnl != null ? pos.ib_daily_pnl : wsDailyPnl;
  return (effectivePnl / Math.abs(closeValue)) * 100;
}

/* ─── Today's P&L (dollars) ──────────────────────────────── */

export function getTodayPnlDollars(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock") {
    const p = prices?.[pos.ticker];
    if (!p || p.last == null || p.last <= 0 || p.close == null || p.close <= 0) return null;
    return (p.last - p.close) * pos.contracts;
  }
  // Prefer IB's per-position daily P&L (handles intraday additions correctly)
  if (pos.ib_daily_pnl != null) return pos.ib_daily_pnl;
  // Same-day position: Today's P&L = Total P&L (position didn't exist yesterday)
  if (isSameDay(pos)) {
    const rtMv = computeRtMv(pos, prices);
    const mv = rtMv ?? resolveMarketValue(pos);
    if (mv == null) return null;
    return mv - resolveEntryCost(pos);
  }
  // Fall back to WS close-based calculation (overnight positions)
  let pnl = 0;
  let hasClose = false;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key && prices ? prices[key] : null;
    const last = (lp?.last != null && lp.last > 0) ? lp.last : (leg.market_price != null && leg.market_price > 0 ? leg.market_price : null);
    if (last == null) return null;
    const close = lp?.close;
    if (close != null && close > 0) {
      const sign = leg.direction === "LONG" ? 1 : -1;
      pnl += sign * (last - close) * leg.contracts * 100;
      hasClose = true;
    }
  }
  return hasClose ? pnl : null;
}
