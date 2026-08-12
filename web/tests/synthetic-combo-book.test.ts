import { describe, expect, it } from "vitest";

import {
  buildSyntheticComboDepth,
  MAX_COMBO_BOOK_TIMESTAMP_SKEW_MS,
} from "@/lib/book/syntheticComboDepth";
import type { DepthBook, DepthLevel } from "@/lib/pricesProtocol";

const level = (price: number, size: number, exchange: string): DepthLevel => ({
  price, size, exchange, marketMaker: null,
});

function book(symbol: string, bid: DepthLevel[], ask: DepthLevel[], timestamp = "2026-08-11T16:00:00.000Z", kind: "option" | "stock" = "option"): DepthBook {
  return { symbol, kind, bid, ask, isSmartDepth: true, feed: "OPRA BBO", entitled: true, timestamp };
}

describe("buildSyntheticComboDepth", () => {
  it("greedily constructs executable debit bid/ask levels and aggregates equal prices", () => {
    const result = buildSyntheticComboDepth("SLV 60/70C", [
      { direction: "LONG", contracts: 2, book: book("SLV_60_C", [level(4.05, 2, "AMEX"), level(4.05, 2, "CBOE")], [level(4.15, 4, "AMEX"), level(4.2, 4, "CBOE")]) },
      { direction: "SHORT", contracts: 4, book: book("SLV_70_C", [level(1.65, 8, "ISE"), level(1.6, 8, "BOX")], [level(1.7, 4, "BOX"), level(1.7, 4, "ISE")]) },
    ]);

    expect(result?.kind).toBe("combo");
    expect(result?.bid.map(({ price, size, exchange, nbbo }) => ({ price, size, exchange, nbbo }))).toEqual([
      { price: 0.65, size: 4, exchange: "AMEX / BOX | CBOE / ISE", nbbo: true },
    ]);
    expect(result?.ask[0]).toMatchObject({ price: 0.85, size: 4, exchange: "AMEX / ISE", nbbo: true });
    expect(result?.nbbo).toMatchObject({ bestBid: 0.65, bestAsk: 0.85, mid: 0.75 });
  });

  it("preserves negative credit prices and caps sorted output at ten levels", () => {
    const long = book("L", Array.from({ length: 12 }, (_, i) => level(1 - i / 100, 1, `L${i}`)), [level(1.1, 12, "LA")]);
    const short = book("S", [level(2, 12, "SB")], Array.from({ length: 12 }, (_, i) => level(2.1 + i / 100, 1, `S${i}`)));
    const result = buildSyntheticComboDepth("CREDIT", [
      { direction: "LONG", contracts: 1, book: long },
      { direction: "SHORT", contracts: 1, book: short },
    ]);
    expect(result?.bid).toHaveLength(10);
    expect(result?.bid[0].price).toBe(-1.1);
    expect(result?.ask[0].price).toBe(-0.9);
  });

  it("preserves zero crossings and handles all-long and all-short books", () => {
    const a = book("A", [level(1, 3, "A")], [level(1.1, 3, "A")]);
    const b = book("B", [level(2, 3, "B")], [level(2.1, 3, "B")]);
    const allLong = buildSyntheticComboDepth("ALL LONG", [
      { direction: "LONG", contracts: 1, book: a },
      { direction: "LONG", contracts: 1, book: b },
    ]);
    const allShort = buildSyntheticComboDepth("ALL SHORT", [
      { direction: "SHORT", contracts: 1, book: a },
      { direction: "SHORT", contracts: 1, book: b },
    ]);
    const crossing = buildSyntheticComboDepth("ZERO", [
      { direction: "LONG", contracts: 1, book: a },
      { direction: "SHORT", contracts: 1, book: book("C", [level(1.05, 3, "C")], [level(1.05, 3, "C")]) },
    ]);
    expect(allLong?.nbbo).toMatchObject({ bestBid: 3, bestAsk: 3.2 });
    expect(allShort?.nbbo).toMatchObject({ bestBid: -3.2, bestAsk: -3 });
    expect(crossing?.nbbo).toMatchObject({ bestBid: -0.05, bestAsk: 0.05, mid: 0 });
    expect(Object.is(crossing?.nbbo?.mid, -0)).toBe(false);
  });

  it("matches three and four legs marginally until the first leg is exhausted", () => {
    const legs = [
      { direction: "LONG" as const, contracts: 1, book: book("A", [level(5, 2, "A1"), level(4, 2, "A2")], [level(5.1, 4, "A")]) },
      { direction: "SHORT" as const, contracts: 1, book: book("B", [level(2, 4, "B")], [level(2.1, 1, "B1"), level(2.2, 3, "B2")]) },
      { direction: "LONG" as const, contracts: 1, book: book("C", [level(1, 3, "C")], [level(1.1, 3, "C")]) },
      { direction: "SHORT" as const, contracts: 1, book: book("D", [level(0.5, 4, "D")], [level(0.6, 4, "D")]) },
    ];
    const three = buildSyntheticComboDepth("THREE", legs.slice(0, 3));
    const four = buildSyntheticComboDepth("FOUR", legs);
    expect(three?.bid.map(({ price, size }) => ({ price, size }))).toEqual([
      { price: 3.9, size: 1 },
      { price: 3.8, size: 1 },
      { price: 2.8, size: 1 },
    ]);
    expect(four?.bid.map(({ price, size }) => ({ price, size }))).toEqual([
      { price: 3.3, size: 1 },
      { price: 3.2, size: 1 },
      { price: 2.2, size: 1 },
    ]);
    expect(four?.bid[0].exchange).toBe("A1 / B1 / C / D");
  });

  it("normalizes ratios by GCD and consolidates duplicate contract identities", () => {
    const a = book("A", [level(5, 12, "A")], [level(5.1, 12, "A")]);
    const b = book("B", [level(2, 12, "B")], [level(2.1, 12, "B")]);
    const result = buildSyntheticComboDepth("RATIO", [
      { direction: "LONG", contracts: 6, book: a },
      { direction: "SHORT", contracts: 2, book: a },
      { direction: "SHORT", contracts: 8, book: b },
      { direction: "LONG", contracts: 2, book: book("ZERO", [level(1, 2, "Z")], [level(1.1, 2, "Z")]) },
      { direction: "SHORT", contracts: 2, book: book("ZERO", [level(1, 2, "Z")], [level(1.1, 2, "Z")]) },
    ]);
    // Consolidated A +4 and B -8 normalize to +1/-2; zero-net identity disappears.
    expect(result?.bid[0]).toMatchObject({ price: 0.8, size: 6 });
    expect(result?.ask[0]).toMatchObject({ price: 1.1, size: 6 });
  });

  it("supports stock components and rejects unsupported book kinds and invalid contracts", () => {
    const option = book("OPT", [level(1, 1, "O")], [level(1.1, 1, "O")]);
    const stock = book("STK", [level(100, 1, "S")], [level(100.1, 1, "S")], undefined, "stock");
    const future = { ...stock, symbol: "FUT", kind: "future" as const };
    expect(buildSyntheticComboDepth("MIXED", [
      { direction: "LONG", contracts: 1, book: stock },
      { direction: "SHORT", contracts: 1, book: option },
    ])).not.toBeNull();
    expect(buildSyntheticComboDepth("BAD", [
      { direction: "LONG", contracts: 1, book: future },
      { direction: "SHORT", contracts: 1, book: option },
    ])).toBeNull();
    for (const contracts of [0, -1, 1.5, Number.NaN]) {
      expect(buildSyntheticComboDepth("BAD", [
        { direction: "LONG", contracts, book: stock },
        { direction: "SHORT", contracts: 1, book: option },
      ])).toBeNull();
    }
  });

  it("rejects unavailable, crossed, and timestamp-skewed inputs", () => {
    const good = book("A", [level(1, 1, "A")], [level(1.1, 1, "A")]);
    const unavailable = { ...good, entitled: false };
    const crossed = book("X", [level(1.2, 1, "X")], [level(1.1, 1, "X")]);
    const invalid = book("I", [level(Number.NaN, 1, "I")], [level(1.1, 1, "I")]);
    const skewed = book("S", [level(1, 1, "S")], [level(1.1, 1, "S")], new Date(Date.parse(good.timestamp) + MAX_COMBO_BOOK_TIMESTAMP_SKEW_MS + 1).toISOString());
    const legs = (other: DepthBook) => [
      { direction: "LONG" as const, contracts: 1, book: good },
      { direction: "SHORT" as const, contracts: 1, book: other },
    ];
    expect(buildSyntheticComboDepth("X", legs(unavailable))).toBeNull();
    expect(buildSyntheticComboDepth("X", legs(crossed))).toBeNull();
    expect(buildSyntheticComboDepth("X", legs(invalid))).toBeNull();
    expect(buildSyntheticComboDepth("X", legs(skewed))).toBeNull();
    expect(buildSyntheticComboDepth("", legs(good))).toBeNull();
    expect(buildSyntheticComboDepth("X", legs({ ...good, timestamp: "invalid" }))).toBeNull();
    expect(buildSyntheticComboDepth("X", [{ direction: "LONG", contracts: 1, book: good }])).toBeNull();
  });
});
