/**
 * Unit tests: relay handling of IB tickOptionComputation.
 *
 * IB streams four computations per option line (live / delayed):
 *   10 / 80  BID_OPTION_COMPUTATION   → IV implied by the bid
 *   11 / 81  ASK_OPTION_COMPUTATION   → IV implied by the ask
 *   12 / 82  LAST_OPTION_COMPUTATION  → greeks off the last trade
 *   13 / 83  MODEL_OPTION             → IB's model greeks, theo price, PV dividend
 *
 * The relay used to drop 10/11 and never published the model price or PV
 * dividend, so the chain could not show bid/ask IV or IB's theoretical value.
 */

import { describe, expect, it } from "vitest";

const handlerPath = new URL("../../scripts/ib_tick_handler.js", import.meta.url).pathname;
const { createPriceData, applyOptionComputation } = await import(handlerPath);

const MODEL = {
  impliedVol: 0.21, delta: 0.51, optPrice: 10.8, pvDividend: 0.42,
  gamma: 0.012, vega: 0.53, theta: -0.071, undPrice: 352.85,
};

describe("applyOptionComputation", () => {
  it("model tick (13) publishes every IB greek plus theo price and PV dividend", () => {
    const d = createPriceData("IWO_20261218_355_C");
    expect(applyOptionComputation(d, 13, MODEL)).toBe(true);
    expect(d).toMatchObject({
      impliedVol: 0.21, delta: 0.51, gamma: 0.012, vega: 0.53, theta: -0.071,
      undPrice: 352.85, optPrice: 10.8, pvDividend: 0.42,
    });
  });

  it("delayed model tick (83) behaves like 13", () => {
    const d = createPriceData("X");
    applyOptionComputation(d, 83, MODEL);
    expect(d.optPrice).toBe(10.8);
    expect(d.delta).toBe(0.51);
  });

  it("bid computation (10 / 80) sets bidIv only", () => {
    for (const tick of [10, 80]) {
      const d = createPriceData("X");
      expect(applyOptionComputation(d, tick, { ...MODEL, impliedVol: 0.19 })).toBe(true);
      expect(d.bidIv).toBe(0.19);
      expect(d.impliedVol).toBeNull();
      expect(d.delta).toBeNull();
      expect(d.optPrice).toBeNull();
    }
  });

  it("ask computation (11 / 81) sets askIv only", () => {
    for (const tick of [11, 81]) {
      const d = createPriceData("X");
      applyOptionComputation(d, tick, { ...MODEL, impliedVol: 0.23 });
      expect(d.askIv).toBe(0.23);
      expect(d.impliedVol).toBeNull();
    }
  });

  it("last computation (12) fills greeks only until a model tick has landed", () => {
    const d = createPriceData("X");
    applyOptionComputation(d, 12, { ...MODEL, delta: 0.4 });
    expect(d.delta).toBe(0.4);
    // The last-trade computation's optPrice is the last print, not a theo value.
    expect(d.optPrice).toBeNull();
    applyOptionComputation(d, 13, MODEL);
    expect(applyOptionComputation(d, 12, { ...MODEL, delta: 0.3 })).toBe(false);
    expect(d.delta).toBe(0.51);
  });

  it("ignores IB sentinels (-1, -2, MAX_VALUE) and non-finite values", () => {
    const d = createPriceData("X");
    applyOptionComputation(d, 13, {
      impliedVol: -1, delta: -2, optPrice: Number.MAX_VALUE, pvDividend: undefined,
      gamma: Number.NaN, vega: 0.5, theta: -0.1, undPrice: 100,
    });
    expect(d.impliedVol).toBeNull();
    expect(d.delta).toBeNull();
    expect(d.optPrice).toBeNull();
    expect(d.pvDividend).toBeNull();
    expect(d.gamma).toBeNull();
    expect(d.vega).toBe(0.5);
  });

  it("keeps negative theta and put delta", () => {
    const d = createPriceData("X");
    applyOptionComputation(d, 13, { ...MODEL, delta: -0.49, theta: -0.2 });
    expect(d.delta).toBe(-0.49);
    expect(d.theta).toBe(-0.2);
  });

  it("keeps a deep-ITM put delta of exactly -1 (IB's greek sentinel is -2)", () => {
    const d = createPriceData("X");
    applyOptionComputation(d, 13, { ...MODEL, delta: -1 });
    expect(d.delta).toBe(-1);
  });

  it("returns false for unrelated tick types", () => {
    const d = createPriceData("X");
    expect(applyOptionComputation(d, 4, MODEL)).toBe(false);
    expect(d.delta).toBeNull();
  });

  it("createPriceData seeds the new fields as null", () => {
    const d = createPriceData("X");
    expect(d.bidIv).toBeNull();
    expect(d.askIv).toBeNull();
    expect(d.optPrice).toBeNull();
    expect(d.pvDividend).toBeNull();
  });
});
