import { describe, expect, it } from "vitest";
import { bsCall, bsPut } from "../lib/blackScholes";
import {
  computeLegImpliedValue,
  computeOrderImpliedValue,
  computePositionImpliedValue,
  yearsToExpiry,
  type LegImpliedInput,
} from "../lib/impliedValue";
import type { PriceData } from "../lib/pricesProtocol";
import type { OpenOrder, PortfolioPosition } from "../lib/types";

const NOW = new Date("2026-04-28T12:00:00Z"); // 8 AM ET pre-market on the AMD example
const AMD_EXPIRY = "2026-05-01";

function pd(over: Partial<PriceData>): PriceData {
  return {
    symbol: "X",
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
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
    timestamp: NOW.toISOString(),
    ...over,
  };
}

function leg(over: Partial<LegImpliedInput> = {}): LegImpliedInput {
  return {
    ticker: "AMD",
    expiry: AMD_EXPIRY,
    strike: 295,
    type: "Put",
    direction: "LONG",
    contracts: 75,
    ...over,
  };
}

describe("yearsToExpiry", () => {
  it("returns positive years for future expiry", () => {
    const T = yearsToExpiry("2026-05-01", NOW);
    expect(T).not.toBeNull();
    expect(T!).toBeGreaterThan(0);
    expect(T!).toBeLessThan(0.02); // ~3 days < 1.1% of a year
  });

  it("returns 0 for past expiry", () => {
    expect(yearsToExpiry("2020-01-01", NOW)).toBe(0);
  });

  it("accepts compact YYYYMMDD", () => {
    const T1 = yearsToExpiry("2026-05-01", NOW);
    const T2 = yearsToExpiry("20260501", NOW);
    expect(T2).toBeCloseTo(T1!, 10);
  });

  it("returns null on garbage", () => {
    expect(yearsToExpiry("not-a-date", NOW)).toBeNull();
    expect(yearsToExpiry("", NOW)).toBeNull();
  });
});

describe("computeLegImpliedValue", () => {
  it("returns null when option IV is missing", () => {
    const r = computeLegImpliedValue(leg(), { AMD: pd({ last: 280 }) }, { now: NOW });
    expect(r.perContract).toBeNull();
    expect(r.notional).toBeNull();
  });

  it("returns null when no spot is resolvable", () => {
    const r = computeLegImpliedValue(
      leg(),
      { AMD_20260501_295_P: pd({ impliedVol: 0.45 }) },
      { now: NOW },
    );
    expect(r.perContract).toBeNull();
  });

  it("returns null for Stock by virtue of position-level filter (leg input lacks 'Stock' type)", () => {
    // computeLegImpliedValue is only valid for Call/Put; the type system enforces this,
    // but ensure invalid strike => null
    const r = computeLegImpliedValue(leg({ strike: 0 }), {}, { now: NOW });
    expect(r.perContract).toBeNull();
  });

  it("uses ticker.last as primary spot source", () => {
    const r = computeLegImpliedValue(
      leg(),
      {
        AMD: pd({ last: 280, bid: 279.5, ask: 280.5 }),
        AMD_20260501_295_P: pd({ impliedVol: 0.45, undPrice: 290 }),
      },
      { now: NOW },
    );
    expect(r.perContract).not.toBeNull();
    expect(r.inputs?.spotSource).toBe("last");
    expect(r.inputs?.S).toBe(280);
  });

  it("falls back to undPrice when ticker.last is missing", () => {
    const r = computeLegImpliedValue(
      leg(),
      {
        AMD: pd({}), // no last/bid/ask
        AMD_20260501_295_P: pd({ impliedVol: 0.45, undPrice: 290 }),
      },
      { now: NOW },
    );
    expect(r.inputs?.spotSource).toBe("undPrice");
    expect(r.inputs?.S).toBe(290);
  });

  it("falls back to ticker mid as last resort", () => {
    const r = computeLegImpliedValue(
      leg(),
      {
        AMD: pd({ bid: 285, ask: 287 }),
        AMD_20260501_295_P: pd({ impliedVol: 0.45 }),
      },
      { now: NOW },
    );
    expect(r.inputs?.spotSource).toBe("mid");
    expect(r.inputs?.S).toBe(286);
  });

  it("matches bsPut for an AMD long put at the streamed inputs", () => {
    const sigma = 0.45;
    const r = computeLegImpliedValue(
      leg({ strike: 295, type: "Put" }),
      {
        AMD: pd({ last: 280 }),
        AMD_20260501_295_P: pd({ impliedVol: sigma }),
      },
      { now: NOW },
    );
    expect(r.perContract).not.toBeNull();
    const T = yearsToExpiry(AMD_EXPIRY, NOW)!;
    expect(r.perContract!).toBeCloseTo(bsPut(280, 295, T, 0, sigma), 6);
  });

  it("respects custom risk-free rate when supplied", () => {
    const out = computeLegImpliedValue(
      leg({ type: "Call", strike: 100, expiry: "2026-10-28" }),
      {
        AMD: pd({ last: 100 }),
        AMD_20261028_100_C: pd({ impliedVol: 0.25 }),
      },
      { now: NOW, riskFreeRate: 0.05 },
    );
    const T = yearsToExpiry("2026-10-28", NOW)!;
    expect(out.perContract!).toBeCloseTo(bsCall(100, 100, T, 0.05, 0.25), 5);
  });

  it("populates notional = perContract × contracts × 100", () => {
    const r = computeLegImpliedValue(
      leg({ contracts: 75 }),
      {
        AMD: pd({ last: 280 }),
        AMD_20260501_295_P: pd({ impliedVol: 0.45 }),
      },
      { now: NOW },
    );
    expect(r.notional).toBeCloseTo(r.perContract! * 75 * 100, 6);
  });
});

/* ─── position aggregation ───────────────────────────── */

function makePosition(over: Partial<PortfolioPosition> = {}): PortfolioPosition {
  return {
    id: 1,
    ticker: "AMD",
    structure: "Long Put",
    structure_type: "Long Put",
    risk_profile: "defined",
    expiry: AMD_EXPIRY,
    contracts: 75,
    direction: "LONG",
    entry_cost: 22500,
    max_risk: 22500,
    market_value: null,
    legs: [
      { direction: "LONG", contracts: 75, type: "Put", strike: 295, entry_cost: 22500, avg_cost: 3.0, market_price: 3.0, market_value: 22500 },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-04-25",
    ...over,
  };
}

describe("computePositionImpliedValue", () => {
  it("returns null result for stock-only position", () => {
    const r = computePositionImpliedValue(makePosition({ structure_type: "Stock", legs: [] }), {});
    expect(r.netPerContract).toBeNull();
  });

  it("returns null if any leg fails (e.g. missing IV)", () => {
    const r = computePositionImpliedValue(
      makePosition(),
      { AMD: pd({ last: 280 }), AMD_20260501_295_P: pd({}) }, // no IV
      { now: NOW },
    );
    expect(r.netPerContract).toBeNull();
    expect(r.perLeg).toEqual([]);
  });

  it("computes long put: netPerContract = +bsPut", () => {
    const r = computePositionImpliedValue(
      makePosition(),
      { AMD: pd({ last: 280 }), AMD_20260501_295_P: pd({ impliedVol: 0.45 }) },
      { now: NOW },
    );
    const T = yearsToExpiry(AMD_EXPIRY, NOW)!;
    const expected = bsPut(280, 295, T, 0, 0.45);
    expect(r.netPerContract!).toBeCloseTo(expected, 6);
    expect(r.perLeg).toHaveLength(1);
  });

  it("vertical call spread: long K1 + short K2 (K1<K2) → positive net", () => {
    const sigma = 0.3;
    const expiry = "2026-06-19";
    const position = makePosition({
      structure: "Bull Call Spread",
      structure_type: "Bull Call Spread",
      expiry,
      contracts: 5,
      legs: [
        { direction: "LONG", contracts: 5, type: "Call", strike: 100, entry_cost: 1500, avg_cost: 3, market_price: 3, market_value: 1500 },
        { direction: "SHORT", contracts: 5, type: "Call", strike: 110, entry_cost: -500, avg_cost: -1, market_price: 1, market_value: -500 },
      ],
    });
    const r = computePositionImpliedValue(
      position,
      {
        AMD: pd({ last: 105 }),
        AMD_20260619_100_C: pd({ impliedVol: sigma }),
        AMD_20260619_110_C: pd({ impliedVol: sigma }),
      },
      { now: NOW },
    );
    const T = yearsToExpiry(expiry, NOW)!;
    const expected = bsCall(105, 100, T, 0, sigma) - bsCall(105, 110, T, 0, sigma);
    expect(r.netPerContract!).toBeCloseTo(expected, 6);
    expect(r.netPerContract!).toBeGreaterThan(0);
  });

  it("netNotional sums signed leg notionals", () => {
    const r = computePositionImpliedValue(
      makePosition(),
      { AMD: pd({ last: 280 }), AMD_20260501_295_P: pd({ impliedVol: 0.45 }) },
      { now: NOW },
    );
    expect(r.netNotional).toBeCloseTo(r.netPerContract! * 75 * 100, 6);
  });

  it("netNotional for long put is positive: +bsPut × 300 × 100", () => {
    const sigma = 0.45;
    const spot = 280;
    const r = computePositionImpliedValue(
      makePosition({ contracts: 300, legs: [
        { direction: "LONG", contracts: 300, type: "Put", strike: 295, entry_cost: 90000, avg_cost: 3.0, market_price: 3.0, market_value: 90000 },
      ] }),
      { AMD: pd({ last: spot }), AMD_20260501_295_P: pd({ impliedVol: sigma }) },
      { now: NOW },
    );
    const T = yearsToExpiry(AMD_EXPIRY, NOW)!;
    const expected = bsPut(spot, 295, T, 0, sigma) * 300 * 100;
    expect(r.netNotional).not.toBeNull();
    expect(r.netNotional!).toBeGreaterThan(0);
    expect(r.netNotional!).toBeCloseTo(expected, 4);
  });

  it("netNotional for bull call spread (long lower + short higher) is positive (debit)", () => {
    const sigma = 0.3;
    const expiry = "2026-06-19";
    const contracts = 5;
    const position = makePosition({
      structure: "Bull Call Spread",
      structure_type: "Bull Call Spread",
      expiry,
      contracts,
      legs: [
        { direction: "LONG", contracts, type: "Call", strike: 100, entry_cost: 1500, avg_cost: 3, market_price: 3, market_value: 1500 },
        { direction: "SHORT", contracts, type: "Call", strike: 110, entry_cost: -500, avg_cost: -1, market_price: 1, market_value: -500 },
      ],
    });
    const r = computePositionImpliedValue(
      position,
      {
        AMD: pd({ last: 105 }),
        AMD_20260619_100_C: pd({ impliedVol: sigma }),
        AMD_20260619_110_C: pd({ impliedVol: sigma }),
      },
      { now: NOW },
    );
    const T = yearsToExpiry(expiry, NOW)!;
    const expected =
      (bsCall(105, 100, T, 0, sigma) - bsCall(105, 110, T, 0, sigma)) * contracts * 100;
    expect(r.netNotional).not.toBeNull();
    expect(r.netNotional!).toBeGreaterThan(0);
    expect(r.netNotional!).toBeCloseTo(expected, 4);
  });
});

/* ─── order aggregation ──────────────────────────────── */

function makeOrder(over: Partial<OpenOrder> = {}, contractOver: Partial<OpenOrder["contract"]> = {}): OpenOrder {
  return {
    orderId: 1,
    permId: 1,
    symbol: "AMD",
    contract: {
      conId: 1,
      symbol: "AMD",
      secType: "OPT",
      strike: 295,
      right: "P",
      expiry: "20260501",
      ...contractOver,
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 1,
    limitPrice: null,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 1,
    avgFillPrice: null,
    tif: "DAY",
    ...over,
  };
}

describe("computeOrderImpliedValue", () => {
  it("returns null on empty input", () => {
    const r = computeOrderImpliedValue([], {}, { now: NOW });
    expect(r.netPerContract).toBeNull();
  });

  it("returns null if any leg has no IV", () => {
    const r = computeOrderImpliedValue(
      [makeOrder()],
      { AMD: pd({ last: 280 }), AMD_20260501_295_P: pd({}) },
      { now: NOW },
    );
    expect(r.netPerContract).toBeNull();
  });

  it("single BUY OPT leg: rounded bsPut", () => {
    const r = computeOrderImpliedValue(
      [makeOrder()],
      { AMD: pd({ last: 280 }), AMD_20260501_295_P: pd({ impliedVol: 0.45 }) },
      { now: NOW },
    );
    const T = yearsToExpiry(AMD_EXPIRY, NOW)!;
    const expected = Math.round(bsPut(280, 295, T, 0, 0.45) * 100) / 100;
    expect(r.netPerContract).toBeCloseTo(expected, 2);
  });

  it("two-leg BAG combo aggregates signed", () => {
    const sigma = 0.3;
    const expiry = "20260619";
    const orders: OpenOrder[] = [
      makeOrder({ orderId: 1, action: "BUY", totalQuantity: 5 }, { strike: 100, right: "C", expiry }),
      makeOrder({ orderId: 2, action: "SELL", totalQuantity: 5 }, { strike: 110, right: "C", expiry }),
    ];
    const r = computeOrderImpliedValue(
      orders,
      {
        AMD: pd({ last: 105 }),
        AMD_20260619_100_C: pd({ impliedVol: sigma }),
        AMD_20260619_110_C: pd({ impliedVol: sigma }),
      },
      { now: NOW },
    );
    const T = yearsToExpiry("2026-06-19", NOW)!;
    const expected =
      Math.round((bsCall(105, 100, T, 0, sigma) - bsCall(105, 110, T, 0, sigma)) * 100) / 100;
    expect(r.netPerContract).toBeCloseTo(expected, 2);
  });

  it("returns null when contract is STK", () => {
    const stockOrder = makeOrder({}, { secType: "STK", strike: null, right: null, expiry: null });
    const r = computeOrderImpliedValue([stockOrder], {}, { now: NOW });
    expect(r.netPerContract).toBeNull();
  });
});

/* ─── σ back-solve fallback (market closed: no streaming impliedVol) ── */

describe("computeLegImpliedValue — σ back-solver fallback", () => {
  it("back-solves σ from yesterday's option close + underlying close when impliedVol is missing", () => {
    const T_today = yearsToExpiry(AMD_EXPIRY, NOW)!;
    const T_yest = T_today + 1 / 365;
    const sigmaTrue = 0.35;
    const S_yest = 285;
    const K = 295;
    const optionClose = bsPut(S_yest, K, T_yest, 0, sigmaTrue);

    const result = computeLegImpliedValue(
      leg({ strike: K, type: "Put" }),
      {
        AMD: pd({ last: 280, close: S_yest }),
        AMD_20260501_295_P: pd({ close: optionClose }),
      },
      { now: NOW },
    );

    expect(result.perContract).not.toBeNull();
    expect(result.inputs?.sigmaSource).toBe("backsolve");
    const expected = bsPut(280, K, T_today, 0, sigmaTrue);
    expect(result.perContract!).toBeCloseTo(expected, 1);
  });

  it("prefers streaming impliedVol when both stream and close-based fallback are available", () => {
    const sigmaStream = 0.5;
    const T_today = yearsToExpiry(AMD_EXPIRY, NOW)!;
    const optionClose = bsPut(285, 295, T_today + 1 / 365, 0, 0.4);

    const result = computeLegImpliedValue(
      leg(),
      {
        AMD: pd({ last: 280, close: 285 }),
        AMD_20260501_295_P: pd({ impliedVol: sigmaStream, close: optionClose }),
      },
      { now: NOW },
    );

    expect(result.inputs?.sigmaSource).toBe("stream");
    expect(result.inputs?.sigma).toBe(sigmaStream);
  });

  it("returns null when neither stream nor close-based fallback inputs are present", () => {
    const result = computeLegImpliedValue(
      leg(),
      {
        AMD: pd({ last: 280 }),
        AMD_20260501_295_P: pd({}),
      },
      { now: NOW },
    );
    expect(result.perContract).toBeNull();
  });

  it("propagates user-supplied riskFreeRate into the back-solve", () => {
    const r = 0.05;
    const T_today = yearsToExpiry(AMD_EXPIRY, NOW)!;
    const T_yest = T_today + 1 / 365;
    const sigmaTrue = 0.4;
    const optionClose = bsPut(285, 295, T_yest, r, sigmaTrue);

    const result = computeLegImpliedValue(
      leg(),
      {
        AMD: pd({ last: 280, close: 285 }),
        AMD_20260501_295_P: pd({ close: optionClose }),
      },
      { now: NOW, riskFreeRate: r },
    );

    expect(result.inputs?.r).toBe(r);
    expect(result.inputs?.sigmaSource).toBe("backsolve");
    expect(result.perContract).not.toBeNull();
    const expected = bsPut(280, 295, T_today, r, sigmaTrue);
    expect(result.perContract!).toBeCloseTo(expected, 1);
  });
});
