import { describe, expect, it } from "vitest";
import {
  type OrderLeg,
  formatExpiry,
  detectStructure,
  computeNetPrice,
  computeNetOptionQuote,
  findAtmStrike,
  getVisibleStrikes,
} from "../lib/optionsChainUtils";
import type { PriceData } from "../lib/pricesProtocol";

/* ─── Helpers ─── */

function makeLeg(overrides: Partial<OrderLeg> & { strike: number; right: "C" | "P" }): OrderLeg {
  return {
    id: `AAPL_20260417_${overrides.strike}_${overrides.right}`,
    action: "BUY",
    quantity: 1,
    expiry: "20260417",
    limitPrice: null,
    ...overrides,
  };
}

function makePriceData(bid: number, ask: number): PriceData {
  return {
    symbol: "AAPL",
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 10,
    askSize: 10,
    volume: 100,
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
    timestamp: new Date().toISOString(),
  };
}

/* ─── formatExpiry ─── */

describe("formatExpiry", () => {
  it("converts YYYYMMDD to YYYY-MM-DD", () => {
    expect(formatExpiry("20260417")).toBe("2026-04-17");
  });

  it("returns input unchanged if not 8 chars", () => {
    expect(formatExpiry("2026-04-17")).toBe("2026-04-17");
    expect(formatExpiry("short")).toBe("short");
  });
});

/* ─── detectStructure ─── */

describe("detectStructure", () => {
  it("returns empty string for no legs", () => {
    expect(detectStructure([])).toBe("");
  });

  it("detects Long Call", () => {
    expect(detectStructure([makeLeg({ strike: 200, right: "C", action: "BUY" })])).toBe("Long Call");
  });

  it("detects Short Put", () => {
    expect(detectStructure([makeLeg({ strike: 200, right: "P", action: "SELL" })])).toBe("Short Put");
  });

  it("detects Bull Call Spread", () => {
    const legs = [
      makeLeg({ strike: 200, right: "C", action: "BUY" }),
      makeLeg({ strike: 210, right: "C", action: "SELL" }),
    ];
    expect(detectStructure(legs)).toBe("Bull Call Spread");
  });

  it("detects Bear Call Spread", () => {
    const legs = [
      makeLeg({ strike: 210, right: "C", action: "BUY" }),
      makeLeg({ strike: 200, right: "C", action: "SELL" }),
    ];
    expect(detectStructure(legs)).toBe("Bear Call Spread");
  });

  it("detects Bear Put Spread", () => {
    const legs = [
      makeLeg({ strike: 210, right: "P", action: "BUY" }),
      makeLeg({ strike: 200, right: "P", action: "SELL" }),
    ];
    expect(detectStructure(legs)).toBe("Bear Put Spread");
  });

  it("detects Bull Put Spread", () => {
    const legs = [
      makeLeg({ strike: 200, right: "P", action: "BUY" }),
      makeLeg({ strike: 210, right: "P", action: "SELL" }),
    ];
    expect(detectStructure(legs)).toBe("Bull Put Spread");
  });

  it("detects Risk Reversal (buy call, sell put)", () => {
    const legs = [
      makeLeg({ strike: 210, right: "C", action: "BUY" }),
      makeLeg({ strike: 190, right: "P", action: "SELL" }),
    ];
    expect(detectStructure(legs)).toBe("Risk Reversal");
  });

  it("detects Synthetic (same strike, opposite actions)", () => {
    const legs = [
      makeLeg({ strike: 200, right: "C", action: "BUY" }),
      makeLeg({ strike: 200, right: "P", action: "SELL" }),
    ];
    expect(detectStructure(legs)).toBe("Synthetic");
  });

  it("detects Long Straddle (same strike, same action, call+put)", () => {
    const legs = [
      makeLeg({ strike: 200, right: "C", action: "BUY" }),
      makeLeg({ strike: 200, right: "P", action: "BUY" }),
    ];
    expect(detectStructure(legs)).toBe("Long Straddle");
  });

  it("detects Short Strangle", () => {
    const legs = [
      makeLeg({ strike: 210, right: "C", action: "SELL" }),
      makeLeg({ strike: 190, right: "P", action: "SELL" }),
    ];
    expect(detectStructure(legs)).toBe("Short Strangle");
  });

  it("detects Calendar Spread (different expiries)", () => {
    const legs = [
      makeLeg({ strike: 200, right: "C", action: "BUY", expiry: "20260417" }),
      makeLeg({ strike: 200, right: "C", action: "SELL", expiry: "20260515" }),
    ];
    expect(detectStructure(legs)).toBe("Calendar Spread");
  });

  it("detects multi-leg combo", () => {
    const legs = [
      makeLeg({ strike: 200, right: "C", action: "BUY" }),
      makeLeg({ strike: 210, right: "C", action: "SELL" }),
      makeLeg({ strike: 190, right: "P", action: "BUY" }),
    ];
    expect(detectStructure(legs)).toBe("3-Leg Combo");
  });
});

/* ─── computeNetPrice ─── */

describe("computeNetPrice", () => {
  it("computes net debit for a long call", () => {
    const legs = [makeLeg({ strike: 200, right: "C", action: "BUY" })];
    const prices: Record<string, PriceData> = {
      AAPL_20260417_200_C: makePriceData(5.0, 5.2),
    };
    const net = computeNetPrice(legs, prices);
    expect(net).toBeCloseTo(5.1, 4); // mid = 5.1, BUY = +5.1
  });

  it("computes net credit for a short put", () => {
    const legs = [makeLeg({ strike: 200, right: "P", action: "SELL" })];
    const prices: Record<string, PriceData> = {
      AAPL_20260417_200_P: makePriceData(3.0, 3.4),
    };
    const net = computeNetPrice(legs, prices);
    expect(net).toBeCloseTo(-3.2, 4); // mid = 3.2, SELL = -3.2
  });

  it("computes net debit for a bull call spread", () => {
    const legs = [
      makeLeg({ strike: 200, right: "C", action: "BUY" }),
      makeLeg({ strike: 210, right: "C", action: "SELL" }),
    ];
    const prices: Record<string, PriceData> = {
      AAPL_20260417_200_C: makePriceData(10.0, 10.2),
      AAPL_20260417_210_C: makePriceData(5.0, 5.4),
    };
    const net = computeNetPrice(legs, prices);
    // BUY 200C mid=10.1, SELL 210C mid=5.2 → net = 10.1 - 5.2 = 4.9
    expect(net).toBeCloseTo(4.9, 4);
  });

  it("returns null when prices are missing", () => {
    const legs = [makeLeg({ strike: 200, right: "C", action: "BUY" })];
    expect(computeNetPrice(legs, {})).toBeNull();
  });

  it("falls back to limitPrice when no WS data", () => {
    const legs = [makeLeg({ strike: 200, right: "C", action: "BUY", limitPrice: 5.5 })];
    const net = computeNetPrice(legs, {});
    expect(net).toBeCloseTo(5.5, 4);
  });

  it("keeps BID <= ASK when both bid/ask should remain ordered after signed combination", () => {
    const legs = [
      makeLeg({ strike: 200, right: "C", action: "BUY" }),
      makeLeg({ strike: 190, right: "P", action: "SELL" }),
    ];
    const prices: Record<string, PriceData> = {
      AAPL_20260417_200_C: makePriceData(10.0, 12.0),
      AAPL_20260417_190_P: makePriceData(100.0, 102.0),
    };
    const net = computeNetOptionQuote(legs, prices, "AAPL");
    expect(net.bid).toBe(90.0);
    expect(net.ask).toBe(90.0);
    expect(net.mid).toBe(90.0);
  });

  it("returns empty values when any leg quote is missing", () => {
    const legs = [
      makeLeg({ strike: 200, right: "C", action: "BUY" }),
      makeLeg({ strike: 190, right: "P", action: "SELL" }),
    ];
    const net = computeNetOptionQuote(legs, {
      AAPL_20260417_200_C: makePriceData(10.0, 12.0),
    }, "AAPL");
    expect(net.bid).toBeNull();
    expect(net.ask).toBeNull();
    expect(net.mid).toBeNull();
  });
});

/* ─── findAtmStrike ─── */

describe("findAtmStrike", () => {
  const strikes = [180, 185, 190, 195, 200, 205, 210, 215, 220];

  it("finds exact ATM strike", () => {
    expect(findAtmStrike(strikes, 200)).toBe(200);
  });

  it("finds closest strike when price is between strikes", () => {
    expect(findAtmStrike(strikes, 197)).toBe(195);
    expect(findAtmStrike(strikes, 198)).toBe(200);
  });

  it("returns null for empty strikes", () => {
    expect(findAtmStrike([], 200)).toBeNull();
  });

  it("handles price below all strikes", () => {
    expect(findAtmStrike(strikes, 170)).toBe(180);
  });

  it("handles price above all strikes", () => {
    expect(findAtmStrike(strikes, 230)).toBe(220);
  });
});

/* ─── getVisibleStrikes ─── */

describe("getVisibleStrikes", () => {
  const strikes = [180, 185, 190, 195, 200, 205, 210, 215, 220, 225, 230];

  it("returns strikes centered on ATM", () => {
    const visible = getVisibleStrikes(strikes, 200, 2);
    expect(visible).toEqual([190, 195, 200, 205, 210]);
  });

  it("clamps to start of array", () => {
    const visible = getVisibleStrikes(strikes, 180, 3);
    expect(visible).toEqual([180, 185, 190, 195]);
  });

  it("clamps to end of array", () => {
    const visible = getVisibleStrikes(strikes, 230, 3);
    expect(visible).toEqual([215, 220, 225, 230]);
  });

  it("returns all strikes if strikesPerSide exceeds array", () => {
    const visible = getVisibleStrikes(strikes, 200, 50);
    expect(visible).toEqual(strikes);
  });

  it("returns empty for empty strikes", () => {
    expect(getVisibleStrikes([], null, 10)).toEqual([]);
  });

  it("centers on middle when ATM is null", () => {
    const visible = getVisibleStrikes(strikes, null, 2);
    // Middle index = 5 → strikes[3..7] = [195, 200, 205, 210, 215]
    expect(visible).toEqual([195, 200, 205, 210, 215]);
  });
});
