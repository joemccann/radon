/**
 * Regression: equity positions opened TODAY must report Today P&L = Total P&L.
 *
 * The same-day entry-cost baseline (web/CLAUDE.md "Daily Change % → Same-day
 * exception") was only wired for OPTIONS. Stocks returned before the
 * `isSameDay` check and always used yesterday's close, so three ETF positions
 * opened the same morning showed a Today P&L that contradicted their own P&L:
 *
 *   QQQ 666 sh @ $717.83, last $718.46 → P&L +$421 but Today P&L −$1,605
 *   SPY 900 sh @ $770.20, last $770.45 → P&L +$223 but Today P&L −$2,322
 *   IWM 800 sh @ $301.00, last $300.94 → P&L −$50   but Today P&L +$768
 *
 * Yesterday's close is meaningless for a position that did not exist
 * yesterday: the pre-entry move belongs to the market, not to the operator.
 */

import { describe, expect, it } from "vitest";

import { computeDayMoveBreakdown } from "../lib/dayMoveBreakdown";
import { getStockDailyChg, getTodayPnlDollars } from "../lib/positionUtils";
import type { PriceData } from "../lib/pricesProtocol";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

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

function makeStock(over: {
  ticker: string;
  shares: number;
  avgEntry: number;
  direction?: "LONG" | "SHORT";
  entryDate?: string;
}): PortfolioPosition {
  const direction = over.direction ?? "LONG";
  // Production convention (`scripts/ib_sync.py`): `contracts` is a positive
  // magnitude and a SHORT's `entry_cost` carries the credit sign, so
  // `mv - entry_cost` is the signed P&L for both directions.
  const notional = over.shares * over.avgEntry;
  const entryCost = direction === "LONG" ? notional : -notional;
  return {
    id: 1,
    ticker: over.ticker,
    structure: `Stock (${over.shares}.0 shares)`,
    structure_type: "Stock",
    risk_profile: "equity",
    expiry: "N/A",
    contracts: over.shares,
    direction,
    entry_cost: entryCost,
    max_risk: direction === "LONG" ? notional : null,
    market_value: null,
    ib_daily_pnl: null,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: over.entryDate ?? todayET(),
    legs: [{
      direction,
      contracts: over.shares,
      type: "Stock",
      strike: null,
      entry_cost: entryCost,
      avg_cost: over.avgEntry,
      market_price: null,
      market_value: null,
    }],
  } as PortfolioPosition;
}

/* ─── Production repro ─────────────────────────────────────── */

const QQQ = makeStock({ ticker: "QQQ", shares: 666, avgEntry: 717.83 });
const QQQ_PRICES = { QQQ: makePriceData({ symbol: "QQQ", last: 718.46, close: 720.87 }) };

const SPY = makeStock({ ticker: "SPY", shares: 900, avgEntry: 770.20 });
const SPY_PRICES = { SPY: makePriceData({ symbol: "SPY", last: 770.45, close: 773.03 }) };

const IWM = makeStock({ ticker: "IWM", shares: 800, avgEntry: 301.00 });
const IWM_PRICES = { IWM: makePriceData({ symbol: "IWM", last: 300.94, close: 299.98 }) };

describe("same-day equity Today P&L", () => {
  it("QQQ opened today reports +$420, not the −$1,605 close-based figure", () => {
    expect(getTodayPnlDollars(QQQ, QQQ_PRICES)).toBeCloseTo(419.58, 2);
  });

  it("SPY opened today reports +$225, not −$2,322", () => {
    expect(getTodayPnlDollars(SPY, SPY_PRICES)).toBeCloseTo(225, 2);
  });

  it("IWM opened today reports the −$48 loss, not a +$768 phantom gain", () => {
    expect(getTodayPnlDollars(IWM, IWM_PRICES)).toBeCloseTo(-48, 2);
  });

  it("a same-day SHORT stock is sign-correct: price below entry is a gain", () => {
    // SHORT 1,000 @ $100 (credit −$100,000), last $98 → +$2,000.
    // Close-based math would have read +$7,000 off a $105 prior close.
    const short = makeStock({ ticker: "MU", shares: 1000, avgEntry: 100, direction: "SHORT" });
    const prices = { MU: makePriceData({ symbol: "MU", last: 98, close: 105 }) };
    expect(getTodayPnlDollars(short, prices)).toBeCloseTo(2000, 2);
  });

  it("an OVERNIGHT stock still uses yesterday's close (control)", () => {
    const overnight = makeStock({
      ticker: "QQQ", shares: 666, avgEntry: 717.83, entryDate: "2026-08-04",
    });
    expect(getTodayPnlDollars(overnight, QQQ_PRICES)).toBeCloseTo(-1605.06, 2);
  });

  it("returns null when no live last price is available", () => {
    expect(getTodayPnlDollars(QQQ, { QQQ: makePriceData({ close: 720.87 }) })).toBeNull();
  });
});

describe("same-day equity Day Chg %", () => {
  it("QQQ opened today measures against entry, not yesterday's close", () => {
    // (718.46 − 717.83) / 717.83 × 100 = +0.0878%
    expect(getStockDailyChg(QQQ, QQQ_PRICES)).toBeCloseTo(0.0878, 3);
  });

  it("an OVERNIGHT stock still measures against yesterday's close (control)", () => {
    const overnight = makeStock({
      ticker: "QQQ", shares: 666, avgEntry: 717.83, entryDate: "2026-08-04",
    });
    expect(getStockDailyChg(overnight, QQQ_PRICES)).toBeCloseTo(-0.3343, 3);
  });

  it("a same-day SHORT stock reads positive when the price falls", () => {
    const short = makeStock({ ticker: "MU", shares: 1000, avgEntry: 100, direction: "SHORT" });
    const prices = { MU: makePriceData({ symbol: "MU", last: 98, close: 105 }) };
    expect(getStockDailyChg(short, prices)).toBeCloseTo(2, 3);
  });
});

/* ─── Account Day P&L aggregate must agree with the table ──── */

function portfolioOf(positions: PortfolioPosition[]): PortfolioData {
  return {
    bankroll: 1_000_000,
    peak_value: 1_000_000,
    last_sync: new Date().toISOString(),
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: positions.length,
    defined_risk_count: positions.length,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    positions,
  } as unknown as PortfolioData;
}

describe("computeDayMoveBreakdown — same-day equities", () => {
  it("totals the entry-baselined P&L so the Day P&L card matches the table", () => {
    const { total, rows } = computeDayMoveBreakdown(
      portfolioOf([QQQ, SPY, IWM]),
      { ...QQQ_PRICES, ...SPY_PRICES, ...IWM_PRICES },
    );
    // 419.58 + 225 − 48 = 596.58 (the buggy close-based total was −3,159)
    expect(total).toBeCloseTo(596.58, 2);
    expect(rows).toHaveLength(3);
    expect(rows.map((r) => r.col2)).toEqual(["same-day", "same-day", "same-day"]);
  });

  it("an overnight equity keeps the close-based row (control)", () => {
    const overnight = makeStock({
      ticker: "QQQ", shares: 666, avgEntry: 717.83, entryDate: "2026-08-04",
    });
    const { total, rows } = computeDayMoveBreakdown(portfolioOf([overnight]), QQQ_PRICES);
    expect(total).toBeCloseTo(-1605.06, 2);
    expect(rows[0].col1).toBe("$720.87");
  });
});
