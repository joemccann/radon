import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";

/* ─── Formatters ──────────────────────────────────────────── */

import { fmtPrice, fmtUsdRound } from "@/lib/format";
export { fmtPrice };
export const fmtUsd = fmtUsdRound;
export const fmtPriceOrCalculated = (n: number, isCalculated: boolean) => isCalculated ? `C${fmtPrice(n)}` : fmtPrice(n);

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
  let netLast = 0;
  for (const leg of position.legs) {
    const key = legPriceKey(ticker, position.expiry, leg);
    if (!key) return null;
    const lp = prices[key];
    if (!lp || lp.bid == null || lp.ask == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    netBid += sign * lp.bid;
    netAsk += sign * lp.ask;
    netLast += sign * (lp.last ?? (lp.bid + lp.ask) / 2);
  }

  const lo = Math.min(netBid, netAsk);
  const hi = Math.max(netBid, netAsk);

  return {
    symbol: ticker,
    last: Math.round(netLast * 100) / 100,
    lastIsCalculated: true,
    bid: Math.round(lo * 100) / 100,
    ask: Math.round(hi * 100) / 100,
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

/* ─── Option daily change ─────────────────────────────────── */

export function getOptionDailyChg(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock" || !prices) return null;

  // Compute WS close-based daily P&L and close value (needed for % calc)
  let wsDailyPnl = 0;
  let closeValue = 0;
  let hasClose = false;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    if (!lp || lp.last == null || lp.last <= 0) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    if (lp.close != null && lp.close > 0) {
      wsDailyPnl += sign * (lp.last - lp.close) * leg.contracts * 100;
      closeValue += sign * lp.close * leg.contracts * 100;
      hasClose = true;
    }
  }
  if (!hasClose || closeValue === 0) return null;

  // Prefer IB's per-position daily P&L (handles intraday additions correctly)
  const effectivePnl = pos.ib_daily_pnl != null ? pos.ib_daily_pnl : wsDailyPnl;
  return (effectivePnl / Math.abs(closeValue)) * 100;
}
