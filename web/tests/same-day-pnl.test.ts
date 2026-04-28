import { describe, it, expect } from "vitest";
import { optionKey } from "../lib/pricesProtocol";
import type { PriceData } from "../lib/pricesProtocol";
import type { PortfolioPosition } from "../lib/types";
import { getOptionDailyChg, getTodayPnlDollars } from "../lib/positionUtils";

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

// BTU Long Put $40 — opened TODAY
// avg_entry = $2.80, last = $3.10, yesterday close = $5.17
// Total P&L = (3.10 - 2.80) * 100 * 100 = +$3,000
// Wrong Today's P&L (close-based) = (3.10 - 5.17) * 100 * 100 = -$20,700
// Correct Today's P&L for same-day = +$3,000 (same as Total P&L)
const btuPut: PortfolioPosition = {
  id: 7,
  ticker: "BTU",
  structure: "Long Put $40",
  structure_type: "Option",
  risk_profile: "defined",
  expiry: "2026-04-17",
  contracts: 100,
  direction: "LONG",
  entry_cost: 28000, // 2.80 * 100 * 100
  max_risk: 28000,
  market_value: 31000,
  ib_daily_pnl: null, // IB didn't provide
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: todayET(), // opened today
  legs: [{
    direction: "LONG" as const,
    contracts: 100,
    type: "Put" as const,
    strike: 40,
    entry_cost: 28000,
    avg_cost: 28000,
    market_price: 3.10,
    market_value: 31000,
  }],
};

const btuKey = optionKey({ symbol: "BTU", expiry: "20260417", strike: 40, right: "P" });

const btuPrices: Record<string, PriceData> = {
  [btuKey]: makePriceData({ symbol: btuKey, last: 3.10, close: 5.17 }),
};

// Overnight position for comparison — opened 5 days ago
const overnightPos: PortfolioPosition = {
  ...btuPut,
  id: 8,
  entry_date: "2026-03-14",
};

const pltrRiskReversal: PortfolioPosition = {
  id: 16,
  ticker: "PLTR",
  structure: "Risk Reversal (P$152.5/C$155.0)",
  structure_type: "Risk Reversal",
  risk_profile: "undefined",
  expiry: "2026-03-27",
  contracts: 20,
  direction: "COMBO",
  entry_cost: -1571.92,
  max_risk: null,
  market_value: -1760.0,
  ib_daily_pnl: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: todayET(),
  legs: [
    {
      direction: "LONG" as const,
      contracts: 20,
      type: "Call" as const,
      strike: 155,
      entry_cost: 5034.01,
      avg_cost: 5034.01,
      market_price: 2.48,
      market_value: 4960,
    },
    {
      direction: "SHORT" as const,
      contracts: 20,
      type: "Put" as const,
      strike: 152.5,
      entry_cost: 6605.93,
      avg_cost: 6605.93,
      market_price: 3.36,
      market_value: 6720,
    },
  ],
};

describe("Same-day position — Today's P&L ($)", () => {
  it("same-day position: Today's P&L equals Total P&L when ib_daily_pnl is null", () => {
    const todayPnl = getTodayPnlDollars(btuPut, btuPrices);
    // Total P&L = market_value - entry_cost = 31000 - 28000 = +3000
    // For same-day, Today P&L must equal Total P&L (not close-based -$20,700)
    expect(todayPnl).not.toBeNull();
    expect(todayPnl).toBeCloseTo(3000, 0);
  });

  it("same-day position ignores ib_daily_pnl and uses entry-cost-based total P&L", () => {
    const posWithIbPnl = { ...btuPut, ib_daily_pnl: 2500 };
    const todayPnl = getTodayPnlDollars(posWithIbPnl, btuPrices);
    // Same-day positions must use total P&L (entry-adjusted), not IB daily
    // to avoid stale/incorrect intraday IB daily values.
    expect(todayPnl).toBeCloseTo(3000, 0);
  });


  it("same-day combo: Today's P&L equals net combo pnl instead of close-based leg math", () => {
    const todayPnl = getTodayPnlDollars(pltrRiskReversal, {});
    expect(todayPnl).not.toBeNull();
    expect(todayPnl).toBeCloseTo(-188.08, 2);
  });

  it("same-day combo with timestamp entry_date uses total P&L", () => {
    const todayCombo = {
      ...pltrRiskReversal,
      ib_daily_pnl: -5000,
      entry_date: `${todayET()}T16:15:00-05:00`,
    };
    const todayPnl = getTodayPnlDollars(todayCombo, {});
    expect(todayPnl).toBeCloseTo(-188.08, 2);
  });

  it("overnight position: uses close-based fallback when ib_daily_pnl is null", () => {
    const todayPnl = getTodayPnlDollars(overnightPos, btuPrices);
    // (3.10 - 5.17) * 100 * 100 = -$20,700 — correct for overnight
    expect(todayPnl).not.toBeNull();
    expect(todayPnl).toBeCloseTo(-20700, 0);
  });
});

describe("Same-day position — Day Change %", () => {
  it("same-day position: daily chg uses entry cost as denominator, not close", () => {
    const chg = getOptionDailyChg(btuPut, btuPrices);
    expect(chg).not.toBeNull();
    // Total P&L = +3000, entry_cost = 28000
    // Same-day daily chg = +3000 / 28000 * 100 = +10.71%
    // NOT -40.04% from close-based calculation
    expect(chg!).toBeGreaterThan(0); // must be positive (price went up from entry)
    expect(Math.abs(chg!)).toBeLessThan(20); // reasonable daily chg
  });

  it("overnight position: uses close-based % (existing behavior)", () => {
    const chg = getOptionDailyChg(overnightPos, btuPrices);
    expect(chg).not.toBeNull();
    // (3.10 - 5.17) * 100 * 100 = -$20,700 / |5.17 * 100 * 100| = -40.04%
    expect(chg!).toBeCloseTo(-40.04, 0);
  });
});

/**
 * REGRESSION INVARIANTS — guard against the recurring "Today P&L is wildly
 * inaccurate for trades opened today" bug class. The frontend trusts
 * `entry_date == today` to flip into the same-day branch; if that signal
 * is reliable, the following identity must hold no matter the leg
 * structure or sign convention:
 *
 *   Today P&L (same-day) ≡ Total P&L  ≡  market_value − entry_cost
 */
describe("Same-day P&L regression invariants", () => {
  it("AMD Risk Reversal opened today: Today P&L equals Total P&L (not −$37k)", () => {
    // Mirrors the production bug from 2026-04-28: a fresh AMD risk reversal
    // P$320/C$330 expiry 2026-05-08 was attributed to an unrelated AMD 295P
    // entry_date in the blotter, flipping the position into the overnight
    // branch and surfacing −$37,650 as Today P&L.
    const today = todayET();
    const expiry = "2026-05-08";
    const amdRR: PortfolioPosition = {
      id: 99,
      ticker: "AMD",
      structure: "Risk Reversal (P$320.0/C$330.0)",
      structure_type: "Risk Reversal",
      risk_profile: "undefined",
      expiry,
      contracts: 50,
      direction: "COMBO",
      entry_cost: 2687,
      max_risk: null,
      market_value: 11000,
      ib_daily_pnl: -8000, // IB sometimes reports nonsense for fresh same-day combos
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: today, // ← the fix in ib_sync.py guarantees this for new positions
      legs: [
        { direction: "LONG", contracts: 50, type: "Call", strike: 330, entry_cost: 80413.83, avg_cost: 1608.28, market_price: 17.20, market_value: 86000 },
        { direction: "SHORT", contracts: 50, type: "Put", strike: 320, entry_cost: 77726.41, avg_cost: 1554.53, market_price: 15.00, market_value: 75000 },
      ],
    };

    // WS prices include yesterday's close — which would produce wrong numbers
    // if the position fell through to the overnight branch. Bid/ask omitted
    // to keep `resolveRealtimePrice` on the `last` path; otherwise the mid
    // shifts the rt-MV by a few dollars (still correct, just noisier in
    // assertions).
    const expiryCompact = expiry.replace(/-/g, "");
    const prices: Record<string, PriceData> = {
      AMD: makePriceData({ symbol: "AMD", last: 327.08, close: 311.39 }),
      [optionKey({ symbol: "AMD", expiry: expiryCompact, strike: 330, right: "C" })]: makePriceData({ last: 17.20, close: 24.95 }),
      [optionKey({ symbol: "AMD", expiry: expiryCompact, strike: 320, right: "P" })]: makePriceData({ last: 15.00, close: 8.40 }),
    };

    // resolveEntryCost on a combo sums signed leg entry_costs:
    //   +|80413.83| − |77726.41| = +2687.42
    const ec = 80413.83 - 77726.41;
    const totalPnl = 11000 - ec; // ≈ +8,312.58
    const todayPnl = getTodayPnlDollars(amdRR, prices);
    expect(todayPnl).not.toBeNull();
    expect(todayPnl!).toBeCloseTo(totalPnl, 0);

    const dayChg = getOptionDailyChg(amdRR, prices);
    expect(dayChg).not.toBeNull();
    expect(dayChg!).toBeCloseTo((totalPnl / Math.abs(ec)) * 100, 1);

    // Hard upper bound on the bug class: Today P&L must NEVER be a large
    // negative number for a position whose Total P&L is positive.
    expect(todayPnl!).toBeGreaterThan(0);
  });

  /**
   * Property-style invariant: for ANY position with `entry_date == today`,
   * Today P&L must equal market_value − entry_cost. The position's leg
   * structure, sign, ib_daily_pnl, or stale close prices must never break
   * this. Iterating across a small but representative sample.
   */
  it("invariant: Today P&L === MV − EC for every same-day position", () => {
    const today = todayET();
    const samples: PortfolioPosition[] = [
      {
        id: 1, ticker: "X", structure: "Long Call $50", structure_type: "Long Call",
        risk_profile: "defined", expiry: "2026-12-19", contracts: 10, direction: "LONG",
        entry_cost: 5000, max_risk: 5000, market_value: 5500, ib_daily_pnl: -999,
        kelly_optimal: null, target: null, stop: null, entry_date: today,
        legs: [{ direction: "LONG", contracts: 10, type: "Call", strike: 50, entry_cost: 5000, avg_cost: 500, market_price: 5.5, market_value: 5500 }],
      },
      {
        id: 2, ticker: "Y", structure: "Short Put $40", structure_type: "Short Put",
        risk_profile: "undefined", expiry: "2026-12-19", contracts: 5, direction: "SHORT",
        entry_cost: -1500, max_risk: null, market_value: -1200, ib_daily_pnl: null,
        kelly_optimal: null, target: null, stop: null, entry_date: today,
        legs: [{ direction: "SHORT", contracts: 5, type: "Put", strike: 40, entry_cost: -1500, avg_cost: -300, market_price: 2.4, market_value: -1200 }],
      },
      {
        id: 3, ticker: "Z", structure: "Bull Call Spread", structure_type: "Bull Call Spread",
        risk_profile: "defined", expiry: "2026-12-19", contracts: 20, direction: "DEBIT",
        entry_cost: 6000, max_risk: 6000, market_value: 9000, ib_daily_pnl: 12345,
        kelly_optimal: null, target: null, stop: null, entry_date: today,
        legs: [
          { direction: "LONG", contracts: 20, type: "Call", strike: 100, entry_cost: 16000, avg_cost: 800, market_price: 10, market_value: 20000 },
          { direction: "SHORT", contracts: 20, type: "Call", strike: 110, entry_cost: -10000, avg_cost: -500, market_price: 5.5, market_value: -11000 },
        ],
      },
    ];

    for (const pos of samples) {
      const totalPnl = (pos.market_value ?? 0) - pos.entry_cost;
      // Provide an empty prices map: forces the same-day branch to fall back
      // to position.market_value, exactly as the UI does on initial render.
      const todayPnl = getTodayPnlDollars(pos, {});
      expect(todayPnl, `${pos.ticker} ${pos.structure}`).not.toBeNull();
      expect(todayPnl!, `${pos.ticker} ${pos.structure}`).toBeCloseTo(totalPnl, 0);
    }
  });
});
