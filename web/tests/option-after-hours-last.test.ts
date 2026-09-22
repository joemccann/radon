/**
 * After-hours option last must be the last trade, else last bid/offer mid.
 *
 * Live 2026-09-21 20:22 ET: META 16 Oct 2026 665 put rendered C$26.70.
 * IB after the close: last=null, bid=ask=-1, CLOSE=26.70 (previous session).
 * 1-minute TRADES last print 8.10 at 15:59 ET; 15:59 midpoint 8.15.
 */
import { describe, it, expect } from "vitest";
import { resolveRealtimePrice } from "../lib/positionUtils";
import type { PriceData } from "../lib/pricesProtocol";

const handlerPath = new URL("../../scripts/ib_tick_handler.js", import.meta.url).pathname;
const {
  createPriceData,
  updatePriceFromTickPrice,
  seedOptionSessionMark,
} = await import(handlerPath);

const TICK = {
  BID: 1, ASK: 2, LAST: 4, CLOSE: 9,
  DELAYED_BID: 66, DELAYED_ASK: 67, DELAYED_LAST: 68, DELAYED_CLOSE: 75,
};

const META = "META_20261016_665_P";
const CLOSE = 26.70;
const PRINT = 8.10;

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

describe("tick handler — after-hours option last", () => {
  it("keeps the META print when IB later sends last=-1, bid/ask=-1, close=26.70", () => {
    const d = createPriceData(META);
    updatePriceFromTickPrice(d, TICK.CLOSE, CLOSE);
    updatePriceFromTickPrice(d, TICK.LAST, PRINT);
    updatePriceFromTickPrice(d, TICK.BID, 8.0);
    updatePriceFromTickPrice(d, TICK.ASK, 8.3);
    expect(d.last).toBe(PRINT);
    expect(d.lastIsCalculated).toBe(false);

    updatePriceFromTickPrice(d, TICK.LAST, -1);
    updatePriceFromTickPrice(d, TICK.BID, -1);
    updatePriceFromTickPrice(d, TICK.ASK, -1);
    updatePriceFromTickPrice(d, TICK.CLOSE, CLOSE);

    expect(d.last).toBe(PRINT);
    expect(d.lastIsCalculated).toBe(false);
    expect(d.close).toBe(CLOSE);
    expect(d.bid).toBeNull();
    expect(d.ask).toBeNull();
  });

  it("a zero LAST does not erase a real print", () => {
    const d = createPriceData(META);
    updatePriceFromTickPrice(d, TICK.LAST, PRINT);
    updatePriceFromTickPrice(d, TICK.LAST, 0);
    expect(d.last).toBe(PRINT);
    expect(d.lastIsCalculated).toBe(false);
  });

  it("delayed sentinels keep the print the same way", () => {
    const d = createPriceData(META);
    updatePriceFromTickPrice(d, TICK.DELAYED_LAST, PRINT);
    updatePriceFromTickPrice(d, TICK.DELAYED_BID, 8.0);
    updatePriceFromTickPrice(d, TICK.DELAYED_ASK, 8.3);
    updatePriceFromTickPrice(d, TICK.DELAYED_LAST, -1);
    updatePriceFromTickPrice(d, TICK.DELAYED_BID, 0);
    updatePriceFromTickPrice(d, TICK.DELAYED_ASK, 0);
    updatePriceFromTickPrice(d, TICK.DELAYED_CLOSE, CLOSE);
    expect(d.last).toBe(PRINT);
    expect(d.lastIsCalculated).toBe(false);
  });

  it("with no print, the last two-sided book becomes the mid after the book dies", () => {
    const d = createPriceData(META);
    updatePriceFromTickPrice(d, TICK.CLOSE, CLOSE);
    updatePriceFromTickPrice(d, TICK.BID, 8.0);
    updatePriceFromTickPrice(d, TICK.ASK, 8.3);
    expect(d.last).toBe(8.15);
    expect(d.lastIsCalculated).toBe(true);

    updatePriceFromTickPrice(d, TICK.BID, 0);
    updatePriceFromTickPrice(d, TICK.ASK, 0);
    expect(d.last).toBe(8.15);
    expect(d.lastIsCalculated).toBe(true);
    expect(d.bid).toBeNull();
    expect(d.ask).toBeNull();
  });

  it("close-only option ticks do not promote previous close to last", () => {
    const d = createPriceData(META);
    updatePriceFromTickPrice(d, TICK.CLOSE, CLOSE);
    updatePriceFromTickPrice(d, TICK.LAST, -1);
    updatePriceFromTickPrice(d, TICK.BID, 0);
    updatePriceFromTickPrice(d, TICK.ASK, 0);
    expect(d.last).toBeNull();
    expect(d.close).toBe(CLOSE);
  });

  it("AAOI frozen last is still replaced by a live book, then the mid survives the book dying", () => {
    const d = createPriceData("AAOI_20260320_105_C");
    updatePriceFromTickPrice(d, TICK.CLOSE, 25.26);
    updatePriceFromTickPrice(d, TICK.LAST, 25.26);
    updatePriceFromTickPrice(d, TICK.BID, 10.30);
    updatePriceFromTickPrice(d, TICK.ASK, 11.70);
    expect(d.last).toBeCloseTo(11.0, 1);
    expect(d.lastIsCalculated).toBe(true);

    updatePriceFromTickPrice(d, TICK.BID, 0);
    updatePriceFromTickPrice(d, TICK.ASK, 0);
    expect(d.last).toBeCloseTo(11.0, 1);
    expect(d.lastIsCalculated).toBe(true);
    expect(d.last).not.toBe(25.26);
  });

  it("seeds a restart from the session-mark cache instead of close", () => {
    const d = createPriceData(META);
    seedOptionSessionMark(d, { last: PRINT, bid: 8.0, ask: 8.3 });
    expect(d.last).toBe(PRINT);
    expect(d.lastIsCalculated).toBe(false);
    updatePriceFromTickPrice(d, TICK.CLOSE, CLOSE);
    updatePriceFromTickPrice(d, TICK.LAST, -1);
    expect(d.last).toBe(PRINT);
  });

  it("does not invent a mid from a one-sided leftover ask", () => {
    const d = createPriceData(META);
    updatePriceFromTickPrice(d, TICK.ASK, 40);
    updatePriceFromTickPrice(d, TICK.BID, 0);
    expect(d.last).toBeNull();
    expect(d.ask).toBe(40);
  });
});

describe("resolveRealtimePrice — option close is not last", () => {
  it("refuses the META previous-session close as last", () => {
    const result = resolveRealtimePrice(makePriceData({
      symbol: META, last: null, bid: null, ask: null, close: CLOSE,
    }));
    expect(result.price).toBeNull();
    expect(result.isPreviousClose).toBe(false);
  });

  it("keeps the last trade when close disagrees and the book is gone", () => {
    const result = resolveRealtimePrice(makePriceData({
      symbol: META, last: PRINT, bid: null, ask: null, close: CLOSE,
    }));
    expect(result.price).toBe(PRINT);
    expect(result.isCalculated).toBe(false);
    expect(result.isPreviousClose).toBe(false);
  });

  it("uses a session mid when that is all the relay has", () => {
    const result = resolveRealtimePrice(makePriceData({
      symbol: META, last: 8.15, lastIsCalculated: true, bid: null, ask: null, close: CLOSE,
    }));
    expect(result.price).toBe(8.15);
    expect(result.isCalculated).toBe(true);
    expect(result.isPreviousClose).toBe(false);
  });

  it("stocks still fall back to previous close", () => {
    const result = resolveRealtimePrice(makePriceData({
      symbol: "META", last: null, bid: null, ask: null, close: 741.24,
    }));
    expect(result.price).toBe(741.24);
    expect(result.isPreviousClose).toBe(true);
  });

  it("sync fallback still wins over a refused close", () => {
    const result = resolveRealtimePrice(makePriceData({
      symbol: META, last: null, bid: null, ask: null, close: CLOSE,
    }), PRINT, false);
    expect(result.price).toBe(PRINT);
    expect(result.isCalculated).toBe(false);
  });
});
