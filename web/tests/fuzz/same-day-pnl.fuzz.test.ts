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
 * ⚠ THE EXPECTED VALUE IS COMPUTED FROM THE GENERATED SPEC, NEVER FROM THE
 * LIBRARY. The first version of this suite asserted
 * `getTodayPnlDollars(pos, prices)` against
 * `getPnlDollars(pos, resolveRealtimeMarketValue(...) ?? resolveMarketValue(...))`,
 * which is byte-for-byte the expression the same-day branch already evaluates.
 * It was an identity: 5000 draws that could not fail. It stayed green while
 * `computeRtMv` was mutated to drop the direction sign — a short position's
 * market value reading POSITIVE, the +$2.19M MU bug — because both sides of
 * the assertion inherited the mutation (T-196).
 *
 * So each quote is generated in a KIND that fixes, by construction, the mark
 * its leg must price at:
 *
 *   tight     last inside a narrow spread            → mark = last
 *   wide      spread >10%, last >5% off the mid      → mark = mid
 *   stale     last outside its own bid/ask           → mark = mid
 *   no-quote  nothing broadcast, synced leg price    → mark = leg.market_price
 *   unpriced  nothing broadcast, no synced price     → position falls to its
 *                                                      synced market value
 *
 * `oracleSameDay` then states the answer as
 * `Σ sign × mark × contracts × 100 − Σ sign × |entry|`, arithmetic the library
 * never touches. A mismatched option key, a lost sign, a market_price
 * preferred over a live quote, a doubled fallback: all of them separate the
 * two sides.
 *
 * Seed pinned to 42 for CI reproducibility; RADON_FUZZ_RANDOM=1 explores. Each
 * property carries an explicit timeout: vitest's 5s default is a per-TEST
 * budget and a property is a thousand cases, which is fine locally and not
 * always fine on a loaded CI runner.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fc from "fast-check";

import {
  getOptionDailyChg,
  getPnlDollars,
  getStockDailyChg,
  getTodayPnlDollars,
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

/** The ET calendar date of a GIVEN instant — never of "now", so the caller
 *  has to say which moment it means. */
function etDate(instant: Date): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(instant);
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** The suite's clock, frozen.
 *
 *  `TODAY` is read once at MODULE LOAD, while `positionUtils.isSameDay` calls
 *  `todayInET()` again at ASSERTION time. A run that crosses 00:00 ET reads
 *  two different dates from those two points: the same-day branch stops
 *  firing mid-suite, the fixture becomes an overnight position, and the
 *  identity goes red for a reason that has nothing to do with the code under
 *  test. Freezing the clock makes both reads the same instant.
 *
 *  Only `Date` is faked — the timer queue stays real so React's scheduling is
 *  untouched. */
const FROZEN_NOW = new Date("2026-08-26T20:00:00Z"); // 2026-08-26 16:00 ET

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(FROZEN_NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

const TODAY = etDate(FROZEN_NOW);
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

/* ─── The spec ────────────────────────────────────────────── */

type QuoteKind = "tight" | "wide" | "stale" | "no-quote" | "unpriced";

/** Marks are whole cents so the reconstructed mid is EXACT: the resolver
 *  returns `Number(((bid + ask) / 2).toFixed(4))`, and for a symmetric spread
 *  around a 2-decimal mark that round-trips to the mark itself for every value
 *  in this range. Nothing here reads the resolver to find that out. */
const arbMark = fc.integer({ min: 20, max: 4_000 }).map((cents) => cents / 100);
const arbMoney = fc.integer({ min: -6_000_000, max: 6_000_000 }).map((cents) => cents / 100);

type LegSpec = {
  direction: "LONG" | "SHORT";
  type: "Call" | "Put";
  contracts: number;
  mark: number;
  syncedMark: number;
  close: number | null;
  entryCost: number;
  ownExpiry: string | null;
  quote: QuoteKind;
};

const arbLeg = fc.record({
  direction: fc.constantFrom("LONG" as const, "SHORT" as const),
  type: fc.constantFrom("Call" as const, "Put" as const),
  contracts: fc.integer({ min: 1, max: 60 }),
  mark: arbMark,
  syncedMark: arbMark,
  close: fc.option(arbMark, { nil: null }),
  entryCost: arbMoney,
  // A leg expiry that disagrees with the position's is a real shape (rolled
  // diagonal) and it moves the price key, which is how one walk can find a
  // quote the other misses.
  ownExpiry: fc.option(fc.constantFrom(...EXPIRIES), { nil: null }),
  quote: fc.constantFrom<QuoteKind>("tight", "wide", "stale", "no-quote", "unpriced"),
});

const arbOptionPosition = fc.record({
  expiry: fc.constantFrom(...EXPIRIES),
  legs: fc.array(arbLeg, { minLength: 1, maxLength: 3 }),
  entryCost: arbMoney,
  ibDailyPnl: fc.option(arbMoney, { nil: null }),
  syncedMv: fc.option(arbMoney, { nil: null }),
});

type OptionSpec = {
  expiry: string;
  legs: LegSpec[];
  entryCost: number;
  ibDailyPnl: number | null;
  syncedMv: number | null;
};

/** Strikes are assigned by leg index, not drawn. Two legs that collide on one
 *  option key share a quote, and a spec whose legs cannot be told apart in the
 *  price map has no well-defined expected value. */
function strikeFor(index: number): number {
  return 560 + index * 10;
}

function dirSign(leg: { direction: "LONG" | "SHORT" }): number {
  return leg.direction === "LONG" ? 1 : -1;
}

/** The `last` each kind broadcasts, or null when nothing is broadcast. The
 *  overnight branch reads this RAW; the same-day branch resolves it to
 *  `leg.mark`. */
function quotedLast(leg: LegSpec): number | null {
  switch (leg.quote) {
    case "tight": return leg.mark;
    case "wide": return leg.mark * 1.06;
    case "stale": return leg.mark * 0.5;
    default: return null;
  }
}

/** The synced per-contract price on the leg. A decoy for the kinds that carry
 *  a live quote: if a walk ever preferred it over the quote, the oracle parts
 *  company with the library. */
function legMarketPrice(leg: LegSpec): number | null {
  if (leg.quote === "no-quote") return leg.mark;
  if (leg.quote === "unpriced") return null;
  return leg.mark + 5;
}

/** The mark the leg MUST price at for the same-day walk, or null when nothing
 *  prices it and the whole position falls back to its synced value. */
function sameDayMark(leg: LegSpec): number | null {
  return leg.quote === "unpriced" ? null : leg.mark;
}

function buildOption(spec: OptionSpec, entryDate: string): {
  pos: PortfolioPosition;
  prices: Record<string, PriceData>;
} {
  const prices: Record<string, PriceData> = {
    [TICKER]: priceData({ symbol: TICKER, last: 577.39, bid: 577.3, ask: 577.5, close: 582.4 }),
  };
  const legs = spec.legs.map((leg, i) => {
    const strike = strikeFor(i);
    const key = optionKey({
      symbol: TICKER,
      expiry: (leg.ownExpiry ?? spec.expiry).replace(/-/g, ""),
      strike,
      right: leg.type === "Call" ? "C" : "P",
    });
    const m = leg.mark;
    const quote =
      leg.quote === "tight" ? { last: m, bid: m - 0.01, ask: m + 0.01 }
      : leg.quote === "wide" ? { last: m * 1.06, bid: m * 0.9, ask: m * 1.1 }
      : leg.quote === "stale" ? { last: m * 0.5, bid: m - 0.05, ask: m + 0.05 }
      : null;
    if (quote) {
      prices[key] = priceData({ symbol: key, ...quote, close: leg.close });
    }
    return {
      direction: leg.direction,
      contracts: leg.contracts,
      type: leg.type,
      strike,
      expiry: leg.ownExpiry,
      entry_cost: leg.entryCost,
      avg_cost: Math.abs(leg.entryCost) / leg.contracts,
      market_price: legMarketPrice(leg),
      market_value: dirSign(leg) * leg.syncedMark * leg.contracts * 100,
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

/* ─── The oracle ──────────────────────────────────────────── */

/** Signed entry cost, restated from the spec: a combo nets its legs on the
 *  credits-negative convention, a single leg carries the position figure. */
function oracleEntryCost(spec: OptionSpec): number {
  if (spec.legs.length > 1) {
    let total = 0;
    for (const leg of spec.legs) total += dirSign(leg) * Math.abs(leg.entryCost);
    return total;
  }
  return spec.entryCost;
}

/** The stale synced market value, restated from the spec. */
function oracleSyncedMv(spec: OptionSpec): number {
  if (spec.legs.length > 1) {
    let total = 0;
    for (const leg of spec.legs) total += dirSign(leg) * leg.syncedMark * leg.contracts * 100;
    return total;
  }
  return spec.syncedMv ?? dirSign(spec.legs[0]) * spec.legs[0].syncedMark * spec.legs[0].contracts * 100;
}

/** The market value the position MUST publish: the live walk when every leg
 *  prices, the synced value when any leg does not. */
function oracleMarketValue(spec: OptionSpec): number {
  if (spec.legs.some((leg) => sameDayMark(leg) == null)) return oracleSyncedMv(spec);
  let total = 0;
  for (const leg of spec.legs) total += dirSign(leg) * sameDayMark(leg)! * leg.contracts * 100;
  return total;
}

/** Today P&L for a SAME-DAY position: market value less what was paid. */
function oracleSameDay(spec: OptionSpec): number {
  return oracleMarketValue(spec) - oracleEntryCost(spec);
}

/** Today P&L for an OVERNIGHT position with no broker figure: the close-based
 *  walk off the RAW broadcast last, null when a leg has no price at all or no
 *  leg carries a usable close. */
function oracleOvernight(spec: OptionSpec): number | null {
  let pnl = 0;
  let hasClose = false;
  for (const leg of spec.legs) {
    const marketPrice = legMarketPrice(leg);
    const raw = quotedLast(leg) ?? (marketPrice != null && marketPrice > 0 ? marketPrice : null);
    if (raw == null) return null;
    if (leg.close != null && leg.close > 0 && quotedLast(leg) != null) {
      pnl += dirSign(leg) * (raw - leg.close) * leg.contracts * 100;
      hasClose = true;
    }
  }
  return hasClose ? pnl : null;
}

/** What every surface renders as the position's P&L. Asserted against the
 *  oracle too, so "Today equals Total" stays a claim about a KNOWN number. */
function surfaceTotalPnl(pos: PortfolioPosition, prices: Record<string, PriceData>): number | null {
  return getPnlDollars(pos, resolveRealtimeMarketValue(pos, prices) ?? resolveMarketValue(pos));
}

describe("same-day P&L identity (property)", () => {
  it("P1 — a same-day option position's Today P&L IS its total P&L", () => {
    fc.assert(
      fc.property(arbOptionPosition, (spec: OptionSpec) => {
        const { pos, prices } = buildOption(spec, TODAY);
        const expected = oracleSameDay(spec);
        expect(getTodayPnlDollars(pos, prices)).toBeCloseTo(expected, 6);
        expect(surfaceTotalPnl(pos, prices)).toBeCloseTo(expected, 6);
      }),
      FC_OPTS,
    );
  }, PROPERTY_TIMEOUT_MS);

  it("P2 — no quote can make a same-day position read its stale ib_daily_pnl", () => {
    fc.assert(
      fc.property(arbOptionPosition, (spec: OptionSpec) => {
        const withBroker = { ...spec, ibDailyPnl: 12_345.67 };
        const { pos, prices } = buildOption(withBroker, TODAY);
        expect(getTodayPnlDollars(pos, prices)).toBeCloseTo(oracleSameDay(withBroker), 6);
      }),
      FC_OPTS,
    );
  }, PROPERTY_TIMEOUT_MS);

  it("P3 — the same-day day-change % is Today P&L over the entry cost", () => {
    fc.assert(
      fc.property(arbOptionPosition, (spec: OptionSpec) => {
        const { pos, prices } = buildOption(spec, TODAY);
        const ec = oracleEntryCost(spec);
        const chg = getOptionDailyChg(pos, prices);
        if (ec === 0) {
          expect(chg).toBeNull();
          return;
        }
        expect(chg).not.toBeNull();
        expect(chg!).toBeCloseTo((oracleSameDay(spec) / Math.abs(ec)) * 100, 6);
      }),
      FC_OPTS,
    );
  }, PROPERTY_TIMEOUT_MS);

  it("P4 — an overnight position is NOT forced onto the entry baseline", () => {
    // The guard against over-correcting: the identity is same-day only. An
    // overnight position reads the broker's figure when it has one, and the
    // close-based walk otherwise — never the entry cost.
    fc.assert(
      fc.property(arbOptionPosition, (spec: OptionSpec) => {
        const { pos, prices } = buildOption(spec, "2026-01-05");
        const today = getTodayPnlDollars(pos, prices);
        if (spec.ibDailyPnl != null) {
          expect(today).toBe(spec.ibDailyPnl);
          return;
        }
        const expected = oracleOvernight(spec);
        if (expected == null) {
          expect(today).toBeNull();
          return;
        }
        expect(today).not.toBeNull();
        expect(today!).toBeCloseTo(expected, 6);
      }),
      FC_OPTS,
    );
    // At least one overnight shape must actually differ from the same-day
    // reading, or P4 proves nothing.
    const overnight: OptionSpec = {
      expiry: "2026-09-18",
      legs: [{
        direction: "SHORT", type: "Put", contracts: 40,
        mark: 3.03, syncedMark: 3.03, close: 3.2,
        entryCost: -12_017, ownExpiry: null, quote: "tight",
      }],
      entryCost: -12_017, ibDailyPnl: null, syncedMv: -12_120,
    };
    const { pos, prices } = buildOption(overnight, "2026-01-05");
    expect(getTodayPnlDollars(pos, prices)).toBeCloseTo(oracleOvernight(overnight)!, 6);
    expect(getTodayPnlDollars(pos, prices)).not.toBe(surfaceTotalPnl(pos, prices));
  }, PROPERTY_TIMEOUT_MS);

  it("P5 — a same-day STOCK position obeys the same identity", () => {
    const arbStock = fc.record({
      direction: fc.constantFrom("LONG" as const, "SHORT" as const),
      shares: fc.integer({ min: 1, max: 2_000 }),
      entryCost: arbMoney,
      last: fc.option(arbMark.map((m) => m * 20), { nil: null }),
      syncedMv: arbMoney,
    });
    fc.assert(
      fc.property(arbStock, (spec) => {
        const prices: Record<string, PriceData> = {};
        if (spec.last != null) {
          prices[TICKER] = priceData({ symbol: TICKER, last: spec.last, close: spec.last * 1.02 });
        }
        const sign = spec.direction === "LONG" ? 1 : -1;
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
            market_price: spec.last, market_value: spec.syncedMv,
          }],
        } as unknown as PortfolioPosition;
        // Sign-aware from the spec: `contracts` is a positive magnitude, so a
        // SHORT's market value must read NEGATIVE.
        const expectedMv = spec.last != null ? sign * spec.last * spec.shares : spec.syncedMv;
        const expected = expectedMv - spec.entryCost;
        expect(getTodayPnlDollars(pos, prices)).toBeCloseTo(expected, 6);
        expect(surfaceTotalPnl(pos, prices)).toBeCloseTo(expected, 6);
        if (spec.entryCost !== 0) {
          expect(getStockDailyChg(pos, prices)).toBeCloseTo((expected / Math.abs(spec.entryCost)) * 100, 6);
        }
      }),
      FC_OPTS,
    );
  }, PROPERTY_TIMEOUT_MS);
});
