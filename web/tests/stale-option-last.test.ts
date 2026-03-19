import { describe, it, expect } from "vitest";
import { resolveRealtimePrice } from "../lib/positionUtils";
import type { PriceData } from "../lib/pricesProtocol";

function makePriceData(overrides: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "TEST", last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("resolveRealtimePrice — stale option last trade", () => {
  it("uses mid when last is below bid (stale last trade)", () => {
    // IWM Call: last=3.24 (stale trade), bid=3.49, ask=3.53
    // last < bid → the last trade is below even the lowest offer → clearly stale
    const pd = makePriceData({
      symbol: "IWM_20260326_247_C",
      last: 3.24,
      bid: 3.49,
      ask: 3.53,
    });
    const result = resolveRealtimePrice(pd);
    // Should use mid = (3.49+3.53)/2 = 3.51, NOT stale last of 3.24
    expect(result.price).toBeCloseTo(3.51, 2);
    expect(result.isCalculated).toBe(true);
  });

  it("uses mid when last is above ask (stale last trade)", () => {
    // last > ask → the last trade is above the current ask → clearly stale
    const pd = makePriceData({
      symbol: "IWM_20260326_243_P",
      last: 5.00,
      bid: 3.15,
      ask: 3.21,
    });
    const result = resolveRealtimePrice(pd);
    expect(result.price).toBeCloseTo(3.18, 2);
    expect(result.isCalculated).toBe(true);
  });

  it("keeps last when it is within bid-ask spread", () => {
    const pd = makePriceData({
      symbol: "IWM_20260326_247_C",
      last: 3.50,
      bid: 3.49,
      ask: 3.53,
    });
    const result = resolveRealtimePrice(pd);
    expect(result.price).toBe(3.50);
    expect(result.isCalculated).toBe(false);
  });

  it("keeps last for stocks (no underscore in symbol)", () => {
    const pd = makePriceData({
      symbol: "IWM",
      last: 244.00,
      bid: 245.50,
      ask: 245.55,
    });
    const result = resolveRealtimePrice(pd);
    expect(result.price).toBe(244.00);
  });
});
