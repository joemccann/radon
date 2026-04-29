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

  it("uses mid when last is inside wide spread but far from mid (>10% spread width)", () => {
    // BTU $39 Put: last=2.85, bid=2.76, ask=3.35, spread=0.59 (19.28%)
    // last is inside bid-ask but spread is very wide and last is near the bottom.
    // mid=3.055, divergence from mid = |2.85-3.055|/3.055 = 6.7%
    // With spread this wide, mid is more representative than stale last.
    const pd = makePriceData({
      symbol: "BTU_20260417_39_P",
      last: 2.85,
      bid: 2.76,
      ask: 3.35,
    });
    const result = resolveRealtimePrice(pd);
    expect(result.price).toBeCloseTo(3.055, 2);
    expect(result.isCalculated).toBe(true);
  });

  it("keeps last when it is within a tight spread", () => {
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

describe("resolveRealtimePrice — close fallback", () => {
  // Scenario: morning sync at 9:50 ET fetched no marketPrice/bid/ask/close for
  // less-liquid options, so leg.market_price is null. WS later streams the
  // previous-session close (or backfills from the disk cache). The row should
  // surface that close so MV / P&L stop rendering "—".
  it("uses close when last/bid/ask and fallbackPrice are all unusable", () => {
    const pd = makePriceData({
      symbol: "AAOI_20260515_105_C",
      last: null, bid: null, ask: null, close: 35.50,
    });
    const result = resolveRealtimePrice(pd, null, false);
    expect(result.price).toBe(35.50);
    expect(result.isCalculated).toBe(true);
  });

  it("uses close when fallbackPrice is undefined", () => {
    const pd = makePriceData({
      symbol: "IGV_20260515_90_C",
      last: null, bid: null, ask: null, close: 1.10,
    });
    const result = resolveRealtimePrice(pd);
    expect(result.price).toBe(1.10);
    expect(result.isCalculated).toBe(true);
  });

  it("prefers fallbackPrice over close when both available", () => {
    // sync's leg.market_price wins — it may be a same-session mid that the
    // WS close-cache hasn't refreshed yet.
    const pd = makePriceData({
      symbol: "AAOI_20260515_105_C",
      last: null, bid: null, ask: null, close: 35.50,
    });
    const result = resolveRealtimePrice(pd, 36.10, true);
    expect(result.price).toBe(36.10);
    expect(result.isCalculated).toBe(true);
  });

  it("ignores zero or negative close values", () => {
    const pd = makePriceData({
      symbol: "AAOI_20260515_105_C",
      last: null, bid: null, ask: null, close: 0,
    });
    expect(resolveRealtimePrice(pd, null, false).price).toBe(null);

    const pd2 = makePriceData({ symbol: "X_20260101_1_C", close: -1 });
    expect(resolveRealtimePrice(pd2, null, false).price).toBe(null);
  });

  it("still prefers a live last over close", () => {
    const pd = makePriceData({
      symbol: "AAPL_20260529_290_C",
      last: 2.13, bid: 2.10, ask: 2.16, close: 2.50,
    });
    const result = resolveRealtimePrice(pd);
    expect(result.price).toBe(2.13);
    expect(result.isCalculated).toBe(false);
  });
});
