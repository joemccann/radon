/**
 * Unit tests: zero-price guard in scripts/ib_tick_handler.js
 *
 * Bug (T-016): normalizeNumber only rejects value < 0, so IB's OTHER
 * "no quote" signal — a literal 0 — survives into PriceData. A dead option
 * book after hours ticks bid=0 / ask=0; updateDerivedLast then averages them
 * into mid=0 and publishes last=0 with lastIsCalculated=true. The relay hands
 * every subscriber a fabricated $0.00 mark on a live position.
 *
 * Two more shapes of the same bug:
 *   - One-sided book (bid=0, ask=0.05) yields a half-book mid of 0.025 even
 *     though updateDerivedLast is explicitly written to require BOTH sides.
 *   - A zero LAST tick over a healthy two-sided book overwrites the real mid
 *     with 0 AND clears lastIsCalculated, so $0.00 renders as a genuine trade.
 *
 * Contract encoded here:
 *   PRICE fields (bid/ask/last/high/low/open/close/52w high/52w low, live and
 *   delayed) treat 0 exactly like the -1 sentinel: no quote → null.
 *   COUNT fields (bidSize/askSize/volume/avgVolume) keep 0 as a real value —
 *   nothing resting on the bid and nothing traded today are both true facts.
 *   A midpoint is derived only from a TWO-SIDED book; there are no half-book
 *   mids (updateDerivedLast already requires bid != null && ask != null —
 *   nulling zero prices is what finally makes that guard mean something).
 */

import { describe, it, expect } from "vitest";

// Dynamic import of ESM module from scripts/ (mirrors ib-delayed-ticks.test.ts)
const handlerPath = new URL("../../scripts/ib_tick_handler.js", import.meta.url).pathname;
const {
  normalizeNumber,
  normalizePrice,
  createPriceData,
  updatePriceFromTickPrice,
  updatePriceFromTickSize,
} = await import(handlerPath);

// IB TICK_TYPE constants (values from @stoqey/ib dist/api/market/tickType)
const TICK = {
  // Live
  BID_SIZE: 0, BID: 1, ASK: 2, ASK_SIZE: 3, LAST: 4,
  HIGH: 6, LOW: 7, VOLUME: 8, CLOSE: 9, OPEN: 14,
  // Misc stats (generic tick 165)
  LOW_52_WEEK: 19, HIGH_52_WEEK: 20, AVG_VOLUME: 21,
  // Delayed
  DELAYED_BID: 66, DELAYED_ASK: 67, DELAYED_LAST: 68,
  DELAYED_BID_SIZE: 69, DELAYED_ASK_SIZE: 70,
  DELAYED_HIGH: 72, DELAYED_LOW: 73,
  DELAYED_VOLUME: 74, DELAYED_CLOSE: 75, DELAYED_OPEN: 76,
};

const OPTION = "SPY_20260918_740_P";

/* ─── The bug: zero prices become a $0.00 mark ───────────────────────────── */

describe("zero PRICE ticks are no-quote, not a price", () => {
  it("bid=0 + ask=0 on an option leaves last null and lastIsCalculated false", () => {
    const d = createPriceData(OPTION);
    updatePriceFromTickPrice(d, TICK.BID, 0);
    updatePriceFromTickPrice(d, TICK.ASK, 0);

    expect(d.bid).toBeNull();
    expect(d.ask).toBeNull();
    expect(d.last).toBeNull();
    expect(d.lastIsCalculated).toBe(false);
  });

  it("bid=0 + ask=0.05 derives NO mid — a midpoint needs a two-sided book", () => {
    // updateDerivedLast requires bid != null AND ask != null. A zero bid is
    // "nobody is bidding", not "the bid is worth $0.00", so the option has one
    // side and no mid. The real ask still publishes so the UI can show it.
    const d = createPriceData(OPTION);
    updatePriceFromTickPrice(d, TICK.BID, 0);
    updatePriceFromTickPrice(d, TICK.ASK, 0.05);

    expect(d.bid).toBeNull();
    expect(d.ask).toBe(0.05);
    expect(d.last).toBeNull();
    expect(d.lastIsCalculated).toBe(false);
  });

  it("delayed ticks follow the same rule (DELAYED_BID/ASK = 0)", () => {
    const d = createPriceData("VIX_20260916_20_C");
    updatePriceFromTickPrice(d, TICK.DELAYED_BID, 0);
    updatePriceFromTickPrice(d, TICK.DELAYED_ASK, 0);

    expect(d.bid).toBeNull();
    expect(d.ask).toBeNull();
    expect(d.last).toBeNull();
    expect(d.lastIsCalculated).toBe(false);
  });

  it("a zero LAST tick never overwrites a healthy two-sided mid", () => {
    const d = createPriceData("AAOI_20260320_105_C");
    updatePriceFromTickPrice(d, TICK.BID, 1.0);
    updatePriceFromTickPrice(d, TICK.ASK, 1.1);
    expect(d.last).toBe(1.05);

    updatePriceFromTickPrice(d, TICK.LAST, 0);

    // The mid survives, still flagged as derived — NOT $0.00 presented as a trade.
    expect(d.last).toBe(1.05);
    expect(d.lastIsCalculated).toBe(true);
  });

  it("a zero CLOSE never becomes a cash index's last", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.CLOSE, 0);

    expect(d.close).toBeNull();
    expect(d.last).toBeNull();
    expect(d.lastIsCalculated).toBe(false);
  });

  it("zero 52-week high/low are dropped (price fields, not counts)", () => {
    const d = createPriceData("SPY");
    updatePriceFromTickPrice(d, TICK.LOW_52_WEEK, 0);
    updatePriceFromTickPrice(d, TICK.HIGH_52_WEEK, 0);

    expect(d.week52Low).toBeNull();
    expect(d.week52High).toBeNull();
  });

  it("last is never 0 for any combination of zero price ticks", () => {
    const sequences: Array<Array<[number, number]>> = [
      [[TICK.BID, 0], [TICK.ASK, 0]],
      [[TICK.BID, 0], [TICK.ASK, 0], [TICK.LAST, 0]],
      [[TICK.BID, 0], [TICK.ASK, 0], [TICK.CLOSE, 0]],
      [[TICK.CLOSE, 0], [TICK.LAST, 0]],
      [[TICK.DELAYED_BID, 0], [TICK.DELAYED_ASK, 0], [TICK.DELAYED_CLOSE, 0]],
      [[TICK.LAST, 0], [TICK.BID, 0], [TICK.ASK, 0.05]],
    ];

    for (const sequence of sequences) {
      for (const symbol of [OPTION, "SPY", "VIX"]) {
        const d = createPriceData(symbol);
        for (const [tickType, value] of sequence) updatePriceFromTickPrice(d, tickType, value);
        expect(d.last, `${symbol} ${JSON.stringify(sequence)}`).toBeNull();
      }
    }
  });

  it("a real bid arriving after a zero bid restores the mid", () => {
    const d = createPriceData(OPTION);
    updatePriceFromTickPrice(d, TICK.BID, 0);
    updatePriceFromTickPrice(d, TICK.ASK, 1.1);
    expect(d.last).toBeNull();

    updatePriceFromTickPrice(d, TICK.BID, 1.0);

    expect(d.bid).toBe(1.0);
    expect(d.last).toBe(1.05);
    expect(d.lastIsCalculated).toBe(true);
  });
});

/* ─── Counts keep zero ────────────────────────────────────────────────────── */

describe("zero COUNT ticks stay valid", () => {
  it("bidSize / askSize of 0 are accepted (empty book side is a fact)", () => {
    const d = createPriceData("SPY");
    updatePriceFromTickSize(d, TICK.BID_SIZE, 0);
    updatePriceFromTickSize(d, TICK.ASK_SIZE, 0);

    expect(d.bidSize).toBe(0);
    expect(d.askSize).toBe(0);
  });

  it("delayed bidSize / askSize of 0 are accepted", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickSize(d, TICK.DELAYED_BID_SIZE, 0);
    updatePriceFromTickSize(d, TICK.DELAYED_ASK_SIZE, 0);

    expect(d.bidSize).toBe(0);
    expect(d.askSize).toBe(0);
  });

  it("volume of 0 is accepted on both the size and the price tick paths", () => {
    const viaSize = createPriceData("SPY");
    updatePriceFromTickSize(viaSize, TICK.VOLUME, 0);
    expect(viaSize.volume).toBe(0);

    const viaPrice = createPriceData("SPY");
    updatePriceFromTickPrice(viaPrice, TICK.VOLUME, 0);
    expect(viaPrice.volume).toBe(0);

    const delayed = createPriceData("SPY");
    updatePriceFromTickPrice(delayed, TICK.DELAYED_VOLUME, 0);
    expect(delayed.volume).toBe(0);
  });

  it("avgVolume of 0 is accepted", () => {
    const d = createPriceData("SPY");
    updatePriceFromTickPrice(d, TICK.AVG_VOLUME, 0);
    expect(d.avgVolume).toBe(0);
  });
});

/* ─── The -1 sentinel is still filtered ───────────────────────────────────── */

describe("the -1 sentinel is still filtered (regression)", () => {
  it("negative prices are dropped on every price field", () => {
    const d = createPriceData(OPTION);
    updatePriceFromTickPrice(d, TICK.BID, -1);
    updatePriceFromTickPrice(d, TICK.ASK, -1);
    updatePriceFromTickPrice(d, TICK.LAST, -1);
    updatePriceFromTickPrice(d, TICK.CLOSE, -1);
    updatePriceFromTickPrice(d, TICK.HIGH, -1);
    updatePriceFromTickPrice(d, TICK.LOW, -1);
    updatePriceFromTickPrice(d, TICK.OPEN, -1);

    expect(d.bid).toBeNull();
    expect(d.ask).toBeNull();
    expect(d.last).toBeNull();
    expect(d.close).toBeNull();
    expect(d.high).toBeNull();
    expect(d.low).toBeNull();
    expect(d.open).toBeNull();
  });

  it("negative sizes are dropped", () => {
    const d = createPriceData("SPY");
    updatePriceFromTickSize(d, TICK.BID_SIZE, -1);
    updatePriceFromTickSize(d, TICK.ASK_SIZE, -1);

    expect(d.bidSize).toBeNull();
    expect(d.askSize).toBeNull();
  });
});

/* ─── Normalizers ─────────────────────────────────────────────────────────── */

describe("normalizePrice vs normalizeNumber", () => {
  it("normalizeNumber keeps 0 — it is the COUNT normalizer", () => {
    expect(normalizeNumber(0)).toBe(0);
    expect(normalizeNumber(100)).toBe(100);
    expect(normalizeNumber(-1)).toBeNull();
  });

  it("normalizePrice rejects 0 and every non-positive / non-finite input", () => {
    expect(normalizePrice(0)).toBeNull();
    expect(normalizePrice(-1)).toBeNull();
    expect(normalizePrice(Number.NaN)).toBeNull();
    expect(normalizePrice(Number.POSITIVE_INFINITY)).toBeNull();
    expect(normalizePrice("1.5")).toBeNull();
    expect(normalizePrice(null)).toBeNull();
    expect(normalizePrice(undefined)).toBeNull();
  });

  it("normalizePrice passes real prices through untouched", () => {
    expect(normalizePrice(0.01)).toBe(0.01);
    expect(normalizePrice(32.49)).toBe(32.49);
    expect(normalizePrice(1e-4)).toBe(1e-4);
  });
});

/* ─── Real quotes are unaffected ──────────────────────────────────────────── */

describe("real quotes are unaffected (regression)", () => {
  it("a two-sided book still derives the mid", () => {
    const d = createPriceData(OPTION);
    updatePriceFromTickPrice(d, TICK.BID, 32.49);
    updatePriceFromTickPrice(d, TICK.ASK, 32.58);

    expect(d.last).toBe(32.535);
    expect(d.lastIsCalculated).toBe(true);
  });

  it("a cash index still falls back to CLOSE", () => {
    const d = createPriceData("VIX");
    updatePriceFromTickPrice(d, TICK.CLOSE, 18.5);

    expect(d.last).toBe(18.5);
    expect(d.lastIsCalculated).toBe(true);
  });

  it("the stale-frozen-LAST override still fires (AAOI shape)", () => {
    const d = createPriceData("AAOI_20260320_105_C");
    updatePriceFromTickPrice(d, TICK.CLOSE, 25.26);
    updatePriceFromTickPrice(d, TICK.LAST, 25.26);
    updatePriceFromTickPrice(d, TICK.BID, 10.3);
    updatePriceFromTickPrice(d, TICK.ASK, 11.7);

    expect(d.last).toBeCloseTo(11.0, 1);
    expect(d.lastIsCalculated).toBe(true);
  });
});
