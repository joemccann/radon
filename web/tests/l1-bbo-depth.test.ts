import { describe, expect, it } from "vitest";
import { buildL1BboDepth } from "../lib/book/l1BboDepth";

describe("buildL1BboDepth", () => {
  it("seeds a one-level entitled book from L1 bid/ask sizes", () => {
    const book = buildL1BboDepth({
      symbol: "CBRS",
      kind: "stock",
      bid: 225,
      ask: 225.4,
      bidSize: 700,
      askSize: 500,
      timestamp: "2026-08-14T16:30:03.000Z",
    });
    expect(book).toMatchObject({
      symbol: "CBRS",
      kind: "stock",
      feed: "L1 BBO",
      entitled: true,
      isSmartDepth: false,
      bid: [{ price: 225, size: 700, marketMaker: null, exchange: null }],
      ask: [{ price: 225.4, size: 500, marketMaker: null, exchange: null }],
      timestamp: "2026-08-14T16:30:03.000Z",
    });
  });

  it("returns null when size is missing or non-positive", () => {
    expect(buildL1BboDepth({
      symbol: "CBRS", kind: "stock", bid: 225, ask: 225.4, bidSize: 0, askSize: 500,
    })).toBeNull();
    expect(buildL1BboDepth({
      symbol: "CBRS", kind: "stock", bid: Number.NaN, ask: 225.4, bidSize: 700, askSize: 500,
    })).toBeNull();
  });
});
