/**
 * Property-based suite for the same-day P&L identity.
 *
 * A position opened today has no yesterday, so its Today P&L IS its total
 * P&L. Example tests pin the structures that have broken (BTU put, PLTR risk
 * reversal, QQQ same-day equity, the 2026-08-26 META short put); this suite
 * proves the identity across the input space, because every regression so far
 * arrived through a DIFFERENT route to the same wrong shape:
 *
 *   - a stock-only inline copy of Today P&L (2026-08-11, equities kept a
 *     close baseline their own P&L column contradicted)
 *   - a card-local real-time market-value walk reading raw `last` while
 *     getTodayPnlDollars read the resolveRealtimePrice mid (2026-08-26, META
 *     40x short $580 put: +$1,097 total against -$103 today)
 *
 * Neither was a bug in the same-day BRANCH. Both were two market values for
 * one position. So the property under test is not "the branch fires" but
 * "there is only one market value": for ANY same-day position and ANY price
 * map, `getTodayPnlDollars` equals `getPnlDollars` off the shared resolver.
 * A future third route — a mismatched option key, a market_price fallback on
 * one side only, a new price field — fails here without anyone predicting it.
 *
 * Seed pinned to 42 for CI reproducibility; RADON_FUZZ_RANDOM=1 explores. Each
 * property carries an explicit timeout: vitest's 5s default is a per-TEST
 * budget and a property is a thousand cases, which is fine locally and not
 * always fine on a loaded CI runner.
 */
import { describe, expect, it } from "vitest";
import fc from "fast-check";

import {
  getOptionDailyChg,
  getPnlDollars,
  getStockDailyChg,
  getTodayPnlDollars,
  resolveEntryCost,
  resolveMarketValue,
  resolveRealtimeMarketValue,
} from "@/lib/positionUtils";
import { optionKey } from "@/lib/pricesProtocol";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";

const FC_OPTS = process.env.RADON_FUZZ_RANDOM === "1"
  ? { numRuns: 1000 }
  : { numRuns: 1000, seed: 42 };
const PROPERTY_TIMEOUT_MS = 60_000;

function todayET(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

const TODAY = todayET();
const TICKER = "META";
const EXPIRIES = ["2026-08-26", "2026-09-18", "2027-01-15"];

function priceData(overrides: Partial<PriceData>): PriceData {
  return {
    symbol: "X", last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: "2026-08-26T16:00:00Z",
    ...overrides,
  };
}

/** A quote wide enough, or stale enough, to make `last` and the mid disagree —
 *  the exact seam the META card fell through. */
const arbQuote = fc.record({
  last: fc.option(fc.double({ min: 0.05, max: 40, noNaN: true }), { nil: null }),
  bid: fc.option(fc.double({ min: 0.01, max: 40, noNaN: true }), { nil: null }),
  ask: fc.option(fc.double({ min: 0.01, max: 45, noNaN: true }), { nil: null }),
  close: fc.option(fc.double({ min: 0.05, max: 40, noNaN: true }), { nil: null }),
  present: fc.boolean(),
});

const arbLeg = fc.record({
  direction: fc.constantFrom("LONG" as const, "SHORT" as const),
  type: fc.constantFrom("Call" as const, "Put" as const),
  strike: fc.constantFrom(560, 570, 580, 590, 600),
  contracts: fc.integer({ min: 1, max: 60 }),
  marketPrice: fc.double({ min: 0.05, max: 40, noNaN: true }),
  entryCost: fc.double({ min: -60_000, max: 60_000, noNaN: true }),
  // A leg expiry that disagrees with the position's is a real shape (rolled
  // diagonal) and it moves the price key, which is how one walk can find a
  // quote the other misses.
  ownExpiry: fc.option(fc.constantFrom(...EXPIRIES), { nil: null }),
  quote: arbQuote,
});

const arbOptionPosition = fc.record({
  expiry: fc.constantFrom(...EXPIRIES),
  legs: fc.array(arbLeg, { minLength: 1, maxLength: 3 }),
  entryCost: fc.double({ min: -60_000, max: 60_000, noNaN: true }),
  ibDailyPnl: fc.option(fc.double({ min: -5_000, max: 5_000, noNaN: true }), { nil: null }),
  syncedMv: fc.option(fc.double({ min: -60_000, max: 60_000, noNaN: true }), { nil: null }),
  underlying: arbQuote,
});

type OptionSpec = ReturnType<typeof arbOptionPosition.generate> extends never ? never : fc.Value<unknown>;

function buildOption(spec: {
  expiry: string;
  legs: Array<{
    direction: "LONG" | "SHORT"; type: "Call" | "Put"; strike: number; contracts: number;
    marketPrice: number; entryCost: number; ownExpiry: string | null;
    quote: { last: number | null; bid: number | null; ask: number | null; close: number | null; present: boolean };
  }>;
  entryCost: number;
  ibDailyPnl: number | null;
  syncedMv: number | null;
  underlying: { last: number | null; bid: number | null; ask: number | null; close: number | null; present: boolean };
}, entryDate: string): { pos: PortfolioPosition; prices: Record<string, PriceData> } {
  const prices: Record<string, PriceData> = {};
  if (spec.underlying.present) {
    prices[TICKER] = priceData({ symbol: TICKER, ...spec.underlying });
  }
  const legs = spec.legs.map((leg) => {
    const effExpiry = (leg.ownExpiry ?? spec.expiry).replace(/-/g, "");
    const key = optionKey({
      symbol: TICKER, expiry: effExpiry, strike: leg.strike,
      right: leg.type === "Call" ? "C" : "P",
    });
    if (leg.quote.present) {
      prices[key] = priceData({
        symbol: key, last: leg.quote.last, bid: leg.quote.bid,
        ask: leg.quote.ask, close: leg.quote.close,
      });
    }
    return {
      direction: leg.direction,
      contracts: leg.contracts,
      type: leg.type,
      strike: leg.strike,
      expiry: leg.ownExpiry,
      entry_cost: leg.entryCost,
      avg_cost: Math.abs(leg.entryCost) / leg.contracts,
      market_price: leg.marketPrice,
      market_value: leg.marketPrice * leg.contracts * 100,
    };
  });
  const pos = {
    id: 1,
    ticker: TICKER,
    structure: "Fuzz",
    structure_type: legs.length > 1 ? "Risk Reversal" : legs[0].direction === "SHORT" ? "Short Put" : "Long Call",
    risk_profile: "undefined",
    expiry: spec.expiry,
    contracts: legs.reduce((m, l) => Math.max(m, l.contracts), 0),
    direction: legs.length > 1 ? "COMBO" : legs[0].direction,
    entry_cost: spec.entryCost,
    max_risk: null,
    market_value: spec.syncedMv,
    ib_daily_pnl: spec.ibDailyPnl,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: entryDate,
    legs,
  } as unknown as PortfolioPosition;
  return { pos, prices };
}

/** What every surface renders as the position's P&L: the shared real-time
 *  market value, with the synced value underneath. */
function surfaceTotalPnl(pos: PortfolioPosition, prices: Record<string, PriceData>): number | null {
  return getPnlDollars(pos, resolveRealtimeMarketValue(pos, prices) ?? resolveMarketValue(pos));
}

describe("same-day P&L identity (property)", () => {
  it("P1 — a same-day option position's Today P&L IS its total P&L", () => {
    fc.assert(
      fc.property(arbOptionPosition, (spec) => {
        const { pos, prices } = buildOption(spec, TODAY);
        expect(getTodayPnlDollars(pos, prices)).toBe(surfaceTotalPnl(pos, prices));
      }),
      FC_OPTS,
    );
  }, PROPERTY_TIMEOUT_MS);

  it("P2 — no quote can make a same-day position read its stale ib_daily_pnl", () => {
    fc.assert(
      fc.property(arbOptionPosition, (spec) => {
        const { pos, prices } = buildOption({ ...spec, ibDailyPnl: 12_345.67 }, TODAY);
        const today = getTodayPnlDollars(pos, prices);
        if (today == null) return;
        expect(today).toBe(surfaceTotalPnl(pos, prices));
      }),
      FC_OPTS,
    );
  }, PROPERTY_TIMEOUT_MS);

  it("P3 — the same-day day-change % is Today P&L over the entry cost", () => {
    fc.assert(
      fc.property(arbOptionPosition, (spec) => {
        const { pos, prices } = buildOption(spec, TODAY);
        const chg = getOptionDailyChg(pos, prices);
        const today = getTodayPnlDollars(pos, prices);
        const ec = resolveEntryCost(pos);
        if (chg == null || today == null || ec === 0) return;
        expect(chg).toBeCloseTo((today / Math.abs(ec)) * 100, 6);
      }),
      FC_OPTS,
    );
  }, PROPERTY_TIMEOUT_MS);

  it("P4 — an overnight position is NOT forced onto the entry baseline", () => {
    // The guard against over-correcting: the identity is same-day only. If a
    // future change makes it hold for every position, the close-based reading
    // has been lost.
    fc.assert(
      fc.property(arbOptionPosition, (spec) => {
        const { pos, prices } = buildOption(spec, "2026-01-05");
        const today = getTodayPnlDollars(pos, prices);
        if (today == null) return;
        expect(Number.isFinite(today)).toBe(true);
      }),
      FC_OPTS,
    );
    // At least one overnight shape must actually differ, or P4 proves nothing.
    const { pos, prices } = buildOption({
      expiry: "2026-09-18",
      legs: [{
        direction: "SHORT", type: "Put", strike: 580, contracts: 40,
        marketPrice: 3.03, entryCost: -12_017, ownExpiry: null,
        quote: { last: 3.03, bid: 3.0, ask: 3.06, close: 3.2, present: true },
      }],
      entryCost: -12_017, ibDailyPnl: null, syncedMv: -12_120,
      underlying: { last: 577.39, bid: null, ask: null, close: 582.4, present: true },
    }, "2026-01-05");
    expect(getTodayPnlDollars(pos, prices)).not.toBe(surfaceTotalPnl(pos, prices));
  }, PROPERTY_TIMEOUT_MS);

  it("P5 — a same-day STOCK position obeys the same identity", () => {
    const arbStock = fc.record({
      direction: fc.constantFrom("LONG" as const, "SHORT" as const),
      shares: fc.integer({ min: 1, max: 2_000 }),
      entryCost: fc.double({ min: -500_000, max: 500_000, noNaN: true }),
      quote: arbQuote,
      syncedMv: fc.option(fc.double({ min: -500_000, max: 500_000, noNaN: true }), { nil: null }),
    });
    fc.assert(
      fc.property(arbStock, (spec) => {
        const prices: Record<string, PriceData> = {};
        if (spec.quote.present) prices[TICKER] = priceData({ symbol: TICKER, ...spec.quote });
        const pos = {
          id: 2, ticker: TICKER, structure: "Stock", structure_type: "Stock",
          risk_profile: "equity", expiry: "N/A", contracts: spec.shares,
          direction: spec.direction, entry_cost: spec.entryCost, max_risk: null,
          market_value: spec.syncedMv, ib_daily_pnl: 999, kelly_optimal: null,
          target: null, stop: null, entry_date: TODAY,
          legs: [{
            direction: spec.direction, contracts: spec.shares, type: "Stock",
            strike: null, entry_cost: spec.entryCost,
            avg_cost: Math.abs(spec.entryCost) / spec.shares,
            market_price: spec.quote.last, market_value: spec.syncedMv,
          }],
        } as unknown as PortfolioPosition;
        expect(getTodayPnlDollars(pos, prices)).toBe(surfaceTotalPnl(pos, prices));
        const chg = getStockDailyChg(pos, prices);
        const today = getTodayPnlDollars(pos, prices);
        const ec = resolveEntryCost(pos);
        if (chg != null && today != null && ec !== 0) {
          expect(chg).toBeCloseTo((today / Math.abs(ec)) * 100, 6);
        }
      }),
      FC_OPTS,
    );
  }, PROPERTY_TIMEOUT_MS);
});
