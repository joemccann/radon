import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { legPriceKey } from "@/lib/positionUtils";

/* ─── Types ───────────────────────────────────────────── */

export type ExposureBreakdownLeg = {
  type: string;
  direction: string;
  strike: number | null;
  contracts: number;
  rawDelta: number | null;
  legDelta: number;
};

export type ExposureBreakdownRow = {
  positionId: number;
  ticker: string;
  structure: string;
  spot: number | null;
  delta: number;
  dollarDelta: number;
  marketValue: number;
  deltaSource: "ib" | "approx" | "mixed";
  legs: ExposureBreakdownLeg[];
};

export type ExposureData = {
  netLong: number;
  netShort: number;
  dollarDelta: number;
  netExposurePct: number;
};

export type ExposureDataWithBreakdown = ExposureData & {
  rows: ExposureBreakdownRow[];
};

/* ─── Delta approximation (mirrored from MetricCards) ── */

function approxDelta(spot: number, strike: number, dte: number, type: "Call" | "Put"): number {
  if (spot <= 0 || strike <= 0 || dte <= 0) return type === "Call" ? 0.5 : -0.5;
  const moneyness = (spot - strike) / strike;
  const timeFactor = Math.max(0.1, Math.sqrt(dte / 365));
  const adjusted = moneyness / (0.2 * timeFactor);
  const callDelta = 0.5 + 0.5 * Math.tanh(adjusted * 2);
  return type === "Call" ? callDelta : callDelta - 1;
}

function daysToExpiry(expiry: string): number {
  if (!expiry || expiry === "N/A") return 0;
  const exp = new Date(expiry + "T16:00:00-05:00");
  const now = new Date();
  return Math.max(0, Math.ceil((exp.getTime() - now.getTime()) / 86_400_000));
}

function normalizeProviderOptionDelta(rawDelta: number, type: "Call" | "Put"): number {
  if (type === "Call") return rawDelta < 0 ? Math.abs(rawDelta) : rawDelta;
  if (rawDelta <= 0) return rawDelta;
  return rawDelta <= 1 ? rawDelta - 1 : -rawDelta;
}

/* ─── Per-position delta with leg breakdown ──────────── */

function positionDeltaDetailed(
  pos: PortfolioPosition,
  prices: Record<string, PriceData>,
): { delta: number; deltaSource: "ib" | "approx" | "mixed"; legs: ExposureBreakdownLeg[] } {
  let totalDelta = 0;
  let usedIb = false;
  let usedApprox = false;
  const legs: ExposureBreakdownLeg[] = [];

  for (const leg of pos.legs) {
    const sign = leg.direction === "LONG" ? 1 : -1;

    if (leg.type === "Stock") {
      const legDelta = sign * leg.contracts;
      totalDelta += legDelta;
      usedApprox = true;
      legs.push({
        type: leg.type,
        direction: leg.direction,
        strike: leg.strike,
        contracts: leg.contracts,
        rawDelta: 1,
        legDelta,
      });
      continue;
    }

    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;

    if (lp?.delta != null) {
      const rawOptionDelta = normalizeProviderOptionDelta(
        lp.delta,
        leg.type as "Call" | "Put",
      );
      const legDelta = sign * rawOptionDelta * leg.contracts * 100;
      totalDelta += legDelta;
      usedIb = true;
      legs.push({
        type: leg.type,
        direction: leg.direction,
        strike: leg.strike,
        contracts: leg.contracts,
        rawDelta: sign * rawOptionDelta,
        legDelta,
      });
      continue;
    }

    // Fallback: approximate delta
    const spot = prices[pos.ticker]?.last;
    if (!spot || spot <= 0 || !leg.strike) {
      legs.push({
        type: leg.type,
        direction: leg.direction,
        strike: leg.strike,
        contracts: leg.contracts,
        rawDelta: null,
        legDelta: 0,
      });
      continue;
    }

    const dte = daysToExpiry(pos.expiry);
    const rawOptionDelta = approxDelta(spot, leg.strike, dte, leg.type as "Call" | "Put");
    const legDelta = sign * rawOptionDelta * leg.contracts * 100;
    totalDelta += legDelta;
    usedApprox = true;
    legs.push({
      type: leg.type,
      direction: leg.direction,
      strike: leg.strike,
      contracts: leg.contracts,
      rawDelta: sign * rawOptionDelta,
      legDelta,
    });
  }

  return {
    delta: totalDelta,
    deltaSource: usedIb && usedApprox ? "mixed" : usedIb ? "ib" : "approx",
    legs,
  };
}

/* ─── Main computation ───────────────────────────────── */

export function computeExposureDetailed(
  portfolio: PortfolioData,
  prices: Record<string, PriceData>,
): ExposureDataWithBreakdown {
  let netLong = 0;
  let netShort = 0;
  let dollarDelta = 0;
  const rows: ExposureBreakdownRow[] = [];

  for (const pos of portfolio.positions) {
    const { delta, deltaSource, legs } = positionDeltaDetailed(pos, prices);
    const spot = prices[pos.ticker]?.last ?? null;

    // Dollar delta
    const posDollarDelta = spot && spot > 0 ? delta * spot : 0;
    dollarDelta += posDollarDelta;

    // Market value for net long/short classification
    let mv = 0;
    if (pos.structure_type === "Stock") {
      const p = prices[pos.ticker];
      if (p?.last && p.last > 0) mv = Math.abs(p.last * pos.contracts);
      else if (pos.market_value != null) mv = Math.abs(pos.market_value);
    } else {
      if (pos.market_value != null) {
        mv = Math.abs(pos.market_value);
      } else {
        const legMv = pos.legs.reduce((s, l) => s + Math.abs(l.market_value ?? 0), 0);
        if (legMv > 0) mv = legMv;
      }
    }

    if (delta > 0) netLong += mv;
    else if (delta < 0) netShort += mv;

    rows.push({
      positionId: pos.id,
      ticker: pos.ticker,
      structure: pos.structure,
      spot,
      delta,
      dollarDelta: posDollarDelta,
      marketValue: mv,
      deltaSource,
      legs,
    });
  }

  const netExposurePct = portfolio.bankroll > 0
    ? ((netLong - netShort) / portfolio.bankroll) * 100
    : 0;

  return { netLong, netShort, dollarDelta, netExposurePct, rows };
}
