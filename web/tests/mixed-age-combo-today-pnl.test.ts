/**
 * Mixed-age combo Today P&L.
 *
 * 2026-09-17: sold SPY 760P against an overnight long 740P (May entry,
 * avg $32.51) to make a bull put spread. The combo was stamped
 * entry_date=today + basis_source=mixed, so Today P&L used the same-day
 * identity MV − EC and printed the long put's entire accumulated loss
 * (−$162,085) as today's drawdown.
 *
 * Same-day identity applies only when EVERY leg opened today. Overnight
 * legs use prior close; session_fills legs use fill basis.
 */
import { describe, it, expect } from "vitest";
import { optionKey } from "../lib/pricesProtocol";
import type { PriceData } from "../lib/pricesProtocol";
import type { PortfolioPosition } from "../lib/types";
import { getPnlDollars, getTodayPnlDollars, isSameDay } from "../lib/positionUtils";

function makePriceData(overrides: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "TEST", last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

function todayET(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** Live 2026-09-17 SPY 50x 740/760 bull put spread (screenshot + Turso). */
function spyBullPutSpread(overrides: Partial<PortfolioPosition> = {}): PortfolioPosition {
  return {
    id: 8,
    ticker: "SPY",
    structure: "Bull Put Spread $740.0/$760.0",
    structure_type: "Bull Put Spread",
    risk_profile: "defined",
    expiry: "2026-09-18",
    contracts: 50,
    direction: "DEBIT",
    entry_cost: null,
    max_risk: null,
    market_value: -10000,
    ib_daily_pnl: null,
    basis_source: "mixed",
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: todayET(),
    legs: [
      {
        direction: "LONG", contracts: 50, type: "Put", strike: 740,
        entry_cost: 162535.04, avg_cost: 3250.70075,
        market_price: 0.12, market_value: 600, basis_source: "ib",
      },
      {
        direction: "SHORT", contracts: 50, type: "Put", strike: 760,
        entry_cost: 10450, avg_cost: 209,
        market_price: 2.12, market_value: 10600, basis_source: "session_fills",
      },
    ],
    ...overrides,
  };
}

const EXPIRY = "20260918";
const LONG_KEY = optionKey({ symbol: "SPY", expiry: EXPIRY, strike: 740, right: "P" });
const SHORT_KEY = optionKey({ symbol: "SPY", expiry: EXPIRY, strike: 760, right: "P" });

const PRICES: Record<string, PriceData> = {
  [LONG_KEY]: makePriceData({ symbol: LONG_KEY, last: 0.12, close: 0.20 }),
  [SHORT_KEY]: makePriceData({ symbol: SHORT_KEY, last: 2.12, close: 3.00 }),
};

describe("mixed-age combo Today P&L (SPY 740/760)", () => {
  it("is not a same-day position even when entry_date is today", () => {
    expect(isSameDay(spyBullPutSpread())).toBe(false);
  });

  it("total P&L still sums the independently measured legs (−$162,085)", () => {
    const pos = spyBullPutSpread();
    // Long: 0.12×50×100 − 162535.04 = −161935.04
    // Short: −(2.12×50×100 − 10450) = −150
    expect(getPnlDollars(pos)).toBeCloseTo(-162085.04, 2);
  });

  it("Today P&L is overnight close move + same-day fill P&L, not the −$162k total", () => {
    const pos = spyBullPutSpread();
    const todayPnl = getTodayPnlDollars(pos, PRICES);
    // Long (overnight): (0.12 − 0.20) × 50 × 100 = −400
    // Short (session_fills): −(2.12 × 50 × 100 − 10450) = −150
    expect(todayPnl).toBeCloseTo(-550, 6);
    expect(todayPnl).not.toBeCloseTo(-162085.04, 0);
    // Close-based on the new short would be −1×(2.12−3.00)×50×100 = +4400
    // plus overnight −400 = +4000. That is also wrong.
    expect(todayPnl).not.toBeCloseTo(4000, 0);
  });

  it("prefers ib_daily_pnl when IB already mixed-lot summed the legs", () => {
    const pos = spyBullPutSpread({ ib_daily_pnl: -275 });
    expect(getTodayPnlDollars(pos, PRICES)).toBe(-275);
  });
});
