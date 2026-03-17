/**
 * Unit tests: IB delayed tick type handling for VIX/VVIX/COR1M
 *
 * Problem: ib_realtime_server.js calls reqMarketDataType(4) (Delayed-Frozen).
 * For indexes without a real-time subscription (VIX, VVIX), IB sends delayed
 * tick types (66–76) instead of live ones (1–14). The original switch statement
 * only handled live types — delayed ticks hit `default: break` and were silently
 * dropped, leaving all price fields null forever.
 *
 * These tests verify the fix: delayed tick types populate the same fields as
 * their live counterparts.
 */

import { describe, it, expect } from "vitest";

// Dynamic import of ESM module from scripts/
const handlerPath = new URL("../../scripts/ib_tick_handler.js", import.meta.url).pathname;
const { createPriceData, updatePriceFromTickPrice, updatePriceFromTickSize } = await import(handlerPath);

// IB TICK_TYPE constants (verified via node --input-type=module)
const TICK = {
  // Live
  BID: 1, ASK: 2, LAST: 4, HIGH: 6, LOW: 7, VOLUME: 8, CLOSE: 9, OPEN: 14, BID_SIZE: 0, ASK_SIZE: 3,
  // Delayed
  DELAYED_BID: 66, DELAYED_ASK: 67, DELAYED_LAST: 68,
  DELAYED_BID_SIZE: 69, DELAYED_ASK_SIZE: 70,
  DELAYED_HIGH: 72, DELAYED_LOW: 73,
  DELAYED_VOLUME: 74, DELAYED_CLOSE: 75, DELAYED_OPEN: 76,
};

describe("updatePriceFromTickPrice — live tick types (regression)", () => {
  it("BID (1) sets bid", () => {
    const d = createPriceData("SPY");
    updatePriceFromTickPrice(d, TICK.BID, 560.10);
    expect(d.bid).toBe(560.10);
  });

  it("ASK (2) sets ask", () => {
    const d = createPriceData("SPY");
    updatePriceFromTickPrice(d, TICK.ASK, 560.12);
    expect(d.ask).toBe(560.12);
  });

  it("LAST (4) sets last", () => {
    const d = createPriceData("SPY");
    updatePriceFromTickPrice(d, TICK.LAST, 560.11);
    expect(d.last).toBe(560.11);
    expect(d.lastIsCalculated).toBe(false);
  });

  it("CLOSE (9) sets close and derives last via updateDerivedLast", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.CLOSE, 18.50);
    expect(d.close).toBe(18.50);
    // VIX has no bid/ask — derived last should fall back to close
    expect(d.last).toBe(18.50);
    expect(d.lastIsCalculated).toBe(true);
  });
});

describe("updatePriceFromTickPrice — delayed tick types (VIX/VVIX bug fix)", () => {
  it("DELAYED_BID (66) sets bid", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_BID, 18.40);
    expect(d.bid).toBe(18.40);
  });

  it("DELAYED_ASK (67) sets ask", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_ASK, 18.60);
    expect(d.ask).toBe(18.60);
  });

  it("DELAYED_LAST (68) sets last and clears lastIsCalculated", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_LAST, 18.50);
    expect(d.last).toBe(18.50);
    expect(d.lastIsCalculated).toBe(false);
  });

  it("DELAYED_HIGH (72) sets high", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_HIGH, 20.10);
    expect(d.high).toBe(20.10);
  });

  it("DELAYED_LOW (73) sets low", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_LOW, 17.20);
    expect(d.low).toBe(17.20);
  });

  it("DELAYED_VOLUME (74) sets volume", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_VOLUME, 1234);
    expect(d.volume).toBe(1234);
  });

  it("DELAYED_CLOSE (75) sets close and derives last for cash indexes", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_CLOSE, 18.50);
    expect(d.close).toBe(18.50);
    // No bid/ask → derived last falls back to close
    expect(d.last).toBe(18.50);
    expect(d.lastIsCalculated).toBe(true);
  });

  it("DELAYED_CLOSE (75) sets close and derives last for COR1M", () => {
    const d = createPriceData("COR1M");
    updatePriceFromTickPrice(d, TICK.DELAYED_CLOSE, 31.48);
    expect(d.close).toBe(31.48);
    expect(d.last).toBe(31.48);
    expect(d.lastIsCalculated).toBe(true);
  });

  it("DELAYED_OPEN (76) sets open", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_OPEN, 17.80);
    expect(d.open).toBe(17.80);
  });

  it("DELAYED_CLOSE + DELAYED_BID + DELAYED_ASK: derived last uses mid, not close", () => {
    const d = createPriceData("VVIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_BID, 94.0);
    updatePriceFromTickPrice(d, TICK.DELAYED_ASK, 96.0);
    updatePriceFromTickPrice(d, TICK.DELAYED_CLOSE, 95.0);
    // bid+ask available → mid = 95.0, not the close fallback
    expect(d.bid).toBe(94.0);
    expect(d.ask).toBe(96.0);
    expect(d.last).toBe(95.0);
    expect(d.lastIsCalculated).toBe(true);
  });

  it("negative value (-1 = IB not available) is normalized to null", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.DELAYED_LAST, -1);
    expect(d.last).toBeNull();
  });
});

describe("updatePriceFromTickSize — delayed size types", () => {
  it("DELAYED_BID_SIZE (69) sets bidSize", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickSize(d, TICK.DELAYED_BID_SIZE, 100);
    expect(d.bidSize).toBe(100);
  });

  it("DELAYED_ASK_SIZE (70) sets askSize", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickSize(d, TICK.DELAYED_ASK_SIZE, 200);
    expect(d.askSize).toBe(200);
  });
});
