import { describe, expect, it } from "vitest";
import type { OrderLeg } from "../lib/optionsChainUtils";
import type { PriceData } from "../lib/pricesProtocol";
import { computeShortStrangleSkew } from "../lib/shortStrangleSkew";

function leg(overrides: Partial<OrderLeg> & { right: "C" | "P"; strike: number }): OrderLeg {
  return {
    id: `MU_20260626_${overrides.strike}_${overrides.right}`,
    action: "SELL",
    right: overrides.right,
    strike: overrides.strike,
    expiry: "20260626",
    quantity: 1,
    limitPrice: null,
    ...overrides,
  };
}

function pd(overrides: Partial<PriceData>): PriceData {
  return {
    symbol: overrides.symbol ?? "MU",
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
    timestamp: "2026-06-24T16:00:00.000Z",
    ...overrides,
  };
}

describe("computeShortStrangleSkew", () => {
  it("returns null unless the proposed order is a short strangle", () => {
    expect(computeShortStrangleSkew({
      ticker: "MU",
      legs: [leg({ right: "C", strike: 1250 })],
      prices: {},
      spot: 1050,
      now: new Date("2026-06-24T16:00:00.000Z"),
    })).toBeNull();

    expect(computeShortStrangleSkew({
      ticker: "MU",
      legs: [
        leg({ right: "C", strike: 1050 }),
        leg({ right: "P", strike: 1050 }),
      ],
      prices: {},
      spot: 1050,
      now: new Date("2026-06-24T16:00:00.000Z"),
    })).toBeNull();
  });

  it("reports put-rich skew and negative net delta for a symmetric short strangle", () => {
    const result = computeShortStrangleSkew({
      ticker: "MU",
      legs: [
        leg({ right: "P", strike: 850 }),
        leg({ right: "C", strike: 1250 }),
      ],
      prices: {
        MU: pd({ symbol: "MU", last: 1050 }),
        MU_20260626_850_P: pd({ symbol: "MU_20260626_850_P", impliedVol: 2.19 }),
        MU_20260626_1250_C: pd({ symbol: "MU_20260626_1250_C", impliedVol: 1.84 }),
      },
      now: new Date("2026-06-24T16:00:00.000Z"),
    });

    expect(result).not.toBeNull();
    expect(result!.status).toBe("ready");
    if (result!.status !== "ready") throw new Error("expected ready skew");

    expect(result.skewVolPts).toBeCloseTo(35, 6);
    expect(result.skewSide).toBe("PUT");
    expect(result.netDeltaPerCombo).toBeLessThan(0);
    expect(result.deltaSharesPerCombo).toBeCloseTo(result.netDeltaPerCombo * 100, 6);
    expect(result.call.source).toBe("stream");
    expect(result.put.source).toBe("stream");
  });

  it("ignores invalid provider IV/delta and backsolves from wing marks", () => {
    const result = computeShortStrangleSkew({
      ticker: "MU",
      legs: [
        leg({ right: "P", strike: 850, quantity: 200 }),
        leg({ right: "C", strike: 1250, quantity: 200 }),
      ],
      prices: {
        MU: pd({ symbol: "MU", last: 1050 }),
        MU_20260626_850_P: pd({
          symbol: "MU_20260626_850_P",
          bid: 6.7,
          ask: 7.0,
          impliedVol: 0,
          delta: 1.88,
        }),
        MU_20260626_1250_C: pd({
          symbol: "MU_20260626_1250_C",
          bid: 7.3,
          ask: 7.6,
          impliedVol: 0,
          delta: 1.89,
        }),
      },
      now: new Date("2026-06-24T16:00:00.000Z"),
    });

    expect(result).not.toBeNull();
    expect(result!.status).toBe("ready");
    if (result!.status !== "ready") throw new Error("expected ready skew");

    expect(result.put.iv).toBeGreaterThan(0);
    expect(result.call.iv).toBeGreaterThan(0);
    expect(result.put.delta).toBeGreaterThanOrEqual(-1);
    expect(result.call.delta).toBeLessThanOrEqual(1);
    expect(result.netDeltaPerCombo).toBeLessThan(0);
    expect(result.deltaSharesTotal).toBeLessThan(0);
    expect(result.quantity).toBe(200);
    expect(result.call.source).toBe("mid");
    expect(result.put.source).toBe("mid");
  });

  it("accepts literal zero provider delta instead of treating it as missing", () => {
    const result = computeShortStrangleSkew({
      ticker: "MU",
      legs: [
        leg({ right: "P", strike: 850 }),
        leg({ right: "C", strike: 1250 }),
      ],
      prices: {
        MU: pd({ symbol: "MU", last: 1050 }),
        MU_20260626_850_P: pd({ symbol: "MU_20260626_850_P", impliedVol: 2.0, delta: 0 }),
        MU_20260626_1250_C: pd({ symbol: "MU_20260626_1250_C", impliedVol: 2.0, delta: 0 }),
      },
      now: new Date("2026-06-24T16:00:00.000Z"),
    });

    expect(result).not.toBeNull();
    expect(result!.status).toBe("ready");
    if (result!.status !== "ready") throw new Error("expected ready skew");
    expect(result.call.delta).toBe(0);
    expect(result.put.delta).toBe(0);
  });
});
