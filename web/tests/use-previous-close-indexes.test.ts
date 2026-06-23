import { describe, expect, it } from "vitest";

import type { PriceData } from "../lib/pricesProtocol";
import { shouldBackfillPreviousClose } from "../lib/usePreviousClose";

function makePriceData(symbol: string, overrides: Partial<PriceData> = {}): PriceData {
  return {
    symbol,
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
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("shouldBackfillPreviousClose", () => {
  it("skips websocket-backed regime indexes even when close is missing", () => {
    expect(shouldBackfillPreviousClose("VIX", makePriceData("VIX", { last: 25.5 }))).toBe(false);
    expect(shouldBackfillPreviousClose("VXN", makePriceData("VXN", { last: 32.29 }))).toBe(false);
    expect(shouldBackfillPreviousClose("VVIX", makePriceData("VVIX", { last: 118.2 }))).toBe(false);
    expect(shouldBackfillPreviousClose("COR1M", makePriceData("COR1M", { last: 31.44 }))).toBe(false);
    expect(shouldBackfillPreviousClose("SPX", makePriceData("SPX", { last: 7438.1 }))).toBe(false);
  });

  it("still backfills regular symbols when last exists and close is missing", () => {
    expect(shouldBackfillPreviousClose("SPY", makePriceData("SPY", { last: 560.25 }))).toBe(true);
    expect(shouldBackfillPreviousClose("AAPL", makePriceData("AAPL", { last: 205.17 }))).toBe(true);
  });

  it("skips symbols that already have a close or have no usable live last", () => {
    expect(shouldBackfillPreviousClose("SPY", makePriceData("SPY", { last: 560.25, close: 555.1 }))).toBe(false);
    expect(shouldBackfillPreviousClose("SPY", makePriceData("SPY", { last: null }))).toBe(false);
    expect(shouldBackfillPreviousClose("SPY", makePriceData("SPY", { last: 0 }))).toBe(false);
    expect(shouldBackfillPreviousClose("AAPL_20260320_205_C", makePriceData("AAPL_20260320_205_C", { last: 7.25 }))).toBe(false);
  });
});
