import { describe, expect, it } from "vitest";
import { resolveRegimeStripLiveState } from "../lib/regimeLiveStrip";
import type { PriceData } from "../lib/pricesProtocol";

function makePrice(symbol: string, last: number, close: number): PriceData {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid: last - 0.1,
    ask: last + 0.1,
    bidSize: 100,
    askSize: 100,
    volume: 1000,
    high: last + 1,
    low: last - 1,
    open: last - 0.5,
    close,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T14:35:00.000Z",
  };
}

describe("resolveRegimeStripLiveState", () => {
  it("prefers live VIX, VVIX, SPY, and COR1M websocket prices when available", () => {
    const state = resolveRegimeStripLiveState({
      prices: {
        VIX: makePrice("VIX", 26.4, 24.8),
        VVIX: makePrice("VVIX", 118.2, 120.4),
        SPY: makePrice("SPY", 561.5, 557.2),
        COR1M: makePrice("COR1M", 31.25, 29.8),
      },
      data: {
        vix: 22.1,
        vvix: 110.5,
        spy: 552.2,
        cor1m: 28.4,
        cor1m_previous_close: 30.9,
        cor1m_5d_change: 1.3,
        vvix_vix_ratio: 5.0,
        spx_100d_ma: 555.0,
        spx_distance_pct: -0.4,
        history: [{ cor1m: 28.1 }],
      },
    });

    expect(state.vixValue).toBe(26.4);
    expect(state.vvixValue).toBe(118.2);
    expect(state.spyValue).toBe(561.5);
    expect(state.cor1mValue).toBe(31.25);
    expect(state.vixClose).toBe(24.8);
    expect(state.vvixClose).toBe(120.4);
    expect(state.spyClose).toBe(557.2);
    expect(state.cor1mPreviousClose).toBe(30.9);
    expect(state.hasLiveVix).toBe(true);
    expect(state.hasLiveVvix).toBe(true);
    expect(state.hasLiveCor1m).toBe(true);
    expect(state.vvixVixRatio).toBeCloseTo(118.2 / 26.4, 6);
    expect(state.spxDistancePct).toBeCloseTo(((561.5 / 555.0) - 1) * 100, 6);
  });

  it("continues preferring live websocket values when market state is marked closed", () => {
    const state = resolveRegimeStripLiveState({
      prices: {
        VIX: makePrice("VIX", 26.4, 24.8),
        VVIX: makePrice("VVIX", 118.2, 120.4),
        SPY: makePrice("SPY", 561.5, 557.2),
        COR1M: makePrice("COR1M", 31.25, 29.8),
      },
      data: {
        vix: 22.1,
        vvix: 110.5,
        spy: 552.2,
        cor1m: 28.4,
        cor1m_previous_close: 30.9,
        cor1m_5d_change: 1.3,
        vvix_vix_ratio: 5.0,
        spx_100d_ma: 555.0,
        spx_distance_pct: -0.4,
        history: [{ cor1m: 28.1 }],
      },
    });

    expect(state.vixValue).toBe(26.4);
    expect(state.vvixValue).toBe(118.2);
    expect(state.spyValue).toBe(561.5);
    expect(state.cor1mValue).toBe(31.25);
    expect(state.vixClose).toBe(24.8);
    expect(state.vvixClose).toBe(120.4);
    expect(state.spyClose).toBe(557.2);
    expect(state.cor1mPreviousClose).toBe(30.9);
    expect(state.hasLiveVix).toBe(true);
    expect(state.hasLiveVvix).toBe(true);
    expect(state.hasLiveCor1m).toBe(true);
  });

  it("falls back to the last history COR1M close when an explicit previous close is absent", () => {
    const state = resolveRegimeStripLiveState({
      prices: {
        COR1M: makePrice("COR1M", 31.25, 29.8),
      },
      data: {
        vix: 22.1,
        vvix: 110.5,
        spy: 552.2,
        cor1m: 28.4,
        cor1m_5d_change: 1.3,
        vvix_vix_ratio: 5.0,
        spx_100d_ma: 555.0,
        spx_distance_pct: -0.4,
        history: [{ cor1m: 27.9 }, { cor1m: 28.6 }],
      },
    });

    expect(state.cor1mValue).toBe(31.25);
    expect(state.cor1mPreviousClose).toBe(28.6);
  });
});
