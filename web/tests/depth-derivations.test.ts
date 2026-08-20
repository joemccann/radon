import { describe, expect, it } from "vitest";

import type { DepthBook, DepthLevel, Trade } from "@/lib/pricesProtocol";
import { parseOptionKey, optionKey } from "@/lib/pricesProtocol";
import {
  buildLadderRows,
  classifyTicks,
  deriveBookHeader,
  groupPriceLevels,
  isBestLevel,
  montageFill,
} from "@/lib/book/depthDerivations";

/* ─── Fixtures (fixed literals only — no wall clock, no RNG) ─── */

const STOCK_BID: DepthLevel[] = [
  { price: 152.52, size: 34, marketMaker: "XNMS", exchange: "SMART" },
  { price: 152.52, size: 100, marketMaker: "IEXG", exchange: "SMART" },
  { price: 152.51, size: 100, marketMaker: "XNYS", exchange: "SMART" },
  { price: 152.48, size: 26, marketMaker: "XNMS", exchange: "SMART" },
];

const STOCK_ASK: DepthLevel[] = [
  { price: 152.70, size: 738, marketMaker: "XNMS", exchange: "SMART" },
  { price: 152.70, size: 100, marketMaker: "BATY", exchange: "SMART" },
  { price: 152.75, size: 10, marketMaker: "XNMS", exchange: "SMART" },
];

const FUTURES_BID: DepthLevel[] = [
  { price: 5012.50, size: 137, marketMaker: null, exchange: null },
  { price: 5012.25, size: 295, marketMaker: null, exchange: null },
  { price: 5012.00, size: 263, marketMaker: null, exchange: null },
];

const FUTURES_ASK: DepthLevel[] = [
  { price: 5012.75, size: 142, marketMaker: null, exchange: null },
  { price: 5013.00, size: 318, marketMaker: null, exchange: null },
  { price: 5013.25, size: 274, marketMaker: null, exchange: null },
];

const TAPE: Trade[] = [
  { price: 152.70, size: 1, exchange: "FINY", time: "12:10:29" },
  { price: 152.66, size: 4, exchange: "FINY", time: "12:10:28" },
  { price: 152.66, size: 36, exchange: "FINY", time: "12:10:27" },
  { price: 152.73, size: 5, exchange: "XNYS", time: "12:10:26" },
];

describe("groupPriceLevels", () => {
  it("marks the first row of each distinct price (montage edge marker)", () => {
    const grouped = groupPriceLevels(STOCK_BID);
    expect(grouped.map((r) => r.firstOfLevel)).toEqual([true, false, true, true]);
  });

  it("marks the very first row as the start of a level", () => {
    const grouped = groupPriceLevels(STOCK_ASK);
    expect(grouped[0].firstOfLevel).toBe(true);
  });

  it("treats adjacent (close but unequal) prices as separate levels", () => {
    const adjacent: DepthLevel[] = [
      { price: 10.01, size: 5, marketMaker: null, exchange: null },
      { price: 10.0, size: 5, marketMaker: null, exchange: null },
    ];
    expect(groupPriceLevels(adjacent).map((r) => r.firstOfLevel)).toEqual([
      true,
      true,
    ]);
  });

  it("conserves size: grouped size sum equals raw size sum", () => {
    const rawSum = STOCK_BID.reduce((acc, r) => acc + r.size, 0);
    const groupedSum = groupPriceLevels(STOCK_BID).reduce(
      (acc, r) => acc + r.size,
      0,
    );
    expect(groupedSum).toBe(rawSum);
  });

  it("returns an empty array for an empty side", () => {
    expect(groupPriceLevels([])).toEqual([]);
  });
});

describe("montageFill", () => {
  it("returns intensity in [0,1]", () => {
    for (const level of [...STOCK_BID, ...STOCK_ASK]) {
      const fill = montageFill(level, 738);
      expect(fill).toBeGreaterThanOrEqual(0);
      expect(fill).toBeLessThanOrEqual(1);
    }
  });

  it("returns 1 for the largest level", () => {
    expect(montageFill({ price: 1, size: 738, marketMaker: null, exchange: null }, 738)).toBe(1);
  });

  it("returns 0 when maxSize is non-positive (no divide by zero)", () => {
    expect(montageFill(STOCK_BID[0], 0)).toBe(0);
    expect(montageFill(STOCK_BID[0], -5)).toBe(0);
  });
});

describe("buildLadderRows", () => {
  // Helpers to read only the live (non-placeholder) rows, in render order.
  const live = (rows: ReturnType<typeof buildLadderRows>["askRows"]) =>
    rows.filter((r): r is Extract<typeof r, { placeholder?: false }> => !r.placeholder);

  // A DEEP book: more bid/ask levels than the requested row count.
  const deepBid: DepthLevel[] = Array.from({ length: 14 }, (_, i) => ({
    price: 5012.5 - i * 0.25,
    size: 100 + i,
    marketMaker: null,
    exchange: null,
  }));
  const deepAsk: DepthLevel[] = Array.from({ length: 14 }, (_, i) => ({
    price: 5012.75 + i * 0.25,
    size: 100 + i,
    marketMaker: null,
    exchange: null,
  }));

  it("accrues cumulative size monotonically from the inside out", () => {
    const { bidRows, askRows } = buildLadderRows({
      bid: FUTURES_BID,
      ask: FUTURES_ASK,
    });
    // bidRows are inside -> worst, so cum is non-decreasing across live rows.
    const bidCums = live(bidRows).map((r) => r.cum);
    expect(bidCums).toEqual([137, 137 + 295, 137 + 295 + 263]);
    for (let i = 1; i < bidCums.length; i++) {
      expect(bidCums[i]).toBeGreaterThanOrEqual(bidCums[i - 1]);
    }
    // askRows are emitted worst -> best (reversed), so cum is non-increasing.
    const askCums = live(askRows).map((r) => r.cum);
    expect(askCums).toEqual([142 + 318 + 274, 142 + 318, 142]);
    for (let i = 1; i < askCums.length; i++) {
      expect(askCums[i]).toBeLessThanOrEqual(askCums[i - 1]);
    }
  });

  it("emits asks worst->best (top) and bids best->worst (spine down)", () => {
    const { bidRows, askRows } = buildLadderRows({
      bid: FUTURES_BID,
      ask: FUTURES_ASK,
    });
    const liveAsk = live(askRows);
    const liveBid = live(bidRows);
    expect(liveAsk[0].level.price).toBe(5013.25); // worst live ask at top
    expect(liveAsk[liveAsk.length - 1].level.price).toBe(5012.75); // best ask above spine
    expect(liveBid[0].level.price).toBe(5012.5); // best bid below spine
    expect(liveBid[liveBid.length - 1].level.price).toBe(5012.0); // worst bid
  });

  it("scales fill into [0,1] against the deepest cumulative (placeholders excluded)", () => {
    const result = buildLadderRows({ bid: FUTURES_BID, ask: FUTURES_ASK });
    const liveRows = [...live(result.askRows), ...live(result.bidRows)];
    for (const row of liveRows) {
      expect(row.fill).toBeGreaterThanOrEqual(0);
      expect(row.fill).toBeLessThanOrEqual(1);
      expect(row.fill).toBeCloseTo(row.cum / result.maxCumulative, 10);
    }
    expect(Math.max(...liveRows.map((r) => r.fill))).toBeCloseTo(1, 10);
  });

  it("flags the inside row of each side as best", () => {
    const { bidRows, askRows } = buildLadderRows({
      bid: FUTURES_BID,
      ask: FUTURES_ASK,
    });
    const bestBid = bidRows.find((r) => r.isBest);
    const bestAsk = askRows.find((r) => r.isBest);
    expect(bestBid?.placeholder).toBe(false);
    expect(bestAsk?.placeholder).toBe(false);
    expect(bestBid && !bestBid.placeholder && bestBid.level.price).toBe(5012.5);
    expect(bestAsk && !bestAsk.placeholder && bestAsk.level.price).toBe(5012.75);
    expect(bidRows.filter((r) => r.isBest)).toHaveLength(1);
    expect(askRows.filter((r) => r.isBest)).toHaveLength(1);
  });

  it("maxCumulative is the largest cumulative across both sides", () => {
    const { maxCumulative } = buildLadderRows({
      bid: FUTURES_BID,
      ask: FUTURES_ASK,
    });
    expect(maxCumulative).toBe(142 + 318 + 274); // 734, the deeper (ask) side
  });

  /* ── Spine anchoring: fixed row count + correct-end padding ── */

  it("returns exactly `rows` askRows and `rows` bidRows for a THIN book, padding the far end", () => {
    const rows = 10;
    const { askRows, bidRows } = buildLadderRows(
      { bid: FUTURES_BID, ask: FUTURES_ASK },
      rows,
    );
    // Exactly `rows` per side regardless of how few live levels stream.
    expect(askRows).toHaveLength(rows);
    expect(bidRows).toHaveLength(rows);
    // 3 live levels per side, so 7 placeholders pad each side.
    expect(askRows.filter((r) => r.placeholder)).toHaveLength(rows - 3);
    expect(bidRows.filter((r) => r.placeholder)).toHaveLength(rows - 3);
    // Asks pad the TOP (far end): the first rows are placeholders, live at the bottom.
    expect(askRows.slice(0, rows - 3).every((r) => r.placeholder)).toBe(true);
    expect(askRows.slice(rows - 3).every((r) => !r.placeholder)).toBe(true);
    // Bids pad the BOTTOM (far end): live at the top, placeholders trail.
    expect(bidRows.slice(0, 3).every((r) => !r.placeholder)).toBe(true);
    expect(bidRows.slice(3).every((r) => r.placeholder)).toBe(true);
  });

  it("returns exactly `rows` per side for a DEEP book, taking the best `rows` levels", () => {
    const rows = 10;
    const { askRows, bidRows } = buildLadderRows(
      { bid: deepBid, ask: deepAsk },
      rows,
    );
    expect(askRows).toHaveLength(rows);
    expect(bidRows).toHaveLength(rows);
    // A deep book fills every row — no placeholders needed.
    expect(askRows.some((r) => r.placeholder)).toBe(false);
    expect(bidRows.some((r) => r.placeholder)).toBe(false);
  });

  it("anchors the spine: best ask is the LAST askRow, best bid is the FIRST bidRow (thin AND deep)", () => {
    for (const book of [
      { bid: FUTURES_BID, ask: FUTURES_ASK },
      { bid: deepBid, ask: deepAsk },
    ]) {
      const { askRows, bidRows } = buildLadderRows(book, 10);
      const lastAsk = askRows[askRows.length - 1];
      const firstBid = bidRows[0];
      // The rows adjacent to the spine are always live (never placeholders),
      // so the inside market never reflows away from the divider.
      expect(lastAsk.placeholder).toBeFalsy();
      expect(firstBid.placeholder).toBeFalsy();
      expect(lastAsk.isBest).toBe(true);
      expect(firstBid.isBest).toBe(true);
      // And they carry the true best prices (min ask / max bid of the book).
      const bestAskPrice = Math.min(...book.ask.map((l) => l.price));
      const bestBidPrice = Math.max(...book.bid.map((l) => l.price));
      expect(!lastAsk.placeholder && lastAsk.level.price).toBe(bestAskPrice);
      expect(!firstBid.placeholder && firstBid.level.price).toBe(bestBidPrice);
    }
  });

  it("fill / cumulative ignore placeholder rows (one honest scale on the live levels)", () => {
    const { askRows, bidRows, maxCumulative } = buildLadderRows(
      { bid: FUTURES_BID, ask: FUTURES_ASK },
      10,
    );
    // maxCumulative comes only from real levels, not the padded count.
    expect(maxCumulative).toBe(142 + 318 + 274);
    for (const row of [...askRows, ...bidRows]) {
      if (row.placeholder) {
        expect(row.cum).toBe(0);
        expect(row.fill).toBe(0);
        expect(row.level).toBeNull();
      } else {
        expect(row.fill).toBeCloseTo(row.cum / maxCumulative, 10);
      }
    }
  });

  it("handles an empty book without dividing by zero (all placeholders, spine still anchored)", () => {
    const { askRows, bidRows, maxCumulative } = buildLadderRows(
      { bid: [], ask: [] },
      10,
    );
    expect(askRows).toHaveLength(10);
    expect(bidRows).toHaveLength(10);
    expect(askRows.every((r) => r.placeholder)).toBe(true);
    expect(bidRows.every((r) => r.placeholder)).toBe(true);
    expect(maxCumulative).toBe(1);
  });

  it("handles a one-sided book (bids only): asks all placeholder, bids padded at the bottom", () => {
    const { askRows, bidRows, maxCumulative } = buildLadderRows(
      { bid: FUTURES_BID, ask: [] },
      10,
    );
    expect(askRows).toHaveLength(10);
    expect(bidRows).toHaveLength(10);
    expect(askRows.every((r) => r.placeholder)).toBe(true);
    expect(bidRows.filter((r) => !r.placeholder)).toHaveLength(FUTURES_BID.length);
    expect(maxCumulative).toBe(695);
    const firstBid = bidRows[0];
    expect(!firstBid.placeholder && firstBid.fill).toBeGreaterThan(0);
  });
});

describe("classifyTicks", () => {
  it("applies the tick test up/down/flat against the prior price", () => {
    const tones = classifyTicks(TAPE).map((t) => t.tone);
    expect(tones).toEqual(["flat", "down", "flat", "up"]);
  });

  it("classifies the first trade as flat (no predecessor)", () => {
    expect(classifyTicks(TAPE)[0].tone).toBe("flat");
  });

  it("preserves the original trade fields", () => {
    const [first] = classifyTicks(TAPE);
    expect(first.price).toBe(152.70);
    expect(first.size).toBe(1);
    expect(first.exchange).toBe("FINY");
    expect(first.time).toBe("12:10:29");
  });

  it("returns an empty array for an empty tape", () => {
    expect(classifyTicks([])).toEqual([]);
  });
});

describe("deriveBookHeader (BUG 1 — header reads from the depth book)", () => {
  // The L1 feed here is the MU corruption signature: negative MARK / BID.
  const CORRUPT_L1 = { bid: -80, ask: -79, last: -80, lastLabel: "MARK" };

  const STOCK_BOOK: DepthBook = {
    symbol: "MU",
    kind: "stock",
    isSmartDepth: true,
    feed: "SMART DEPTH",
    entitled: true,
    timestamp: "t",
    bid: [
      { price: 1039.6, size: 3, marketMaker: "NSDQ", exchange: "SMART" },
      { price: 1039.55, size: 5, marketMaker: "ARCA", exchange: "SMART" },
    ],
    ask: [
      { price: 1039.8, size: 4, marketMaker: "NSDQ", exchange: "SMART" },
      { price: 1039.9, size: 2, marketMaker: "BATS", exchange: "SMART" },
    ],
  };

  it("derives bid/ask/mid from the depth book, never the corrupt L1 scalars", () => {
    const head = deriveBookHeader(STOCK_BOOK, CORRUPT_L1);
    expect(head.bid).toBe(1039.6); // best (max) bid across the book
    expect(head.ask).toBe(1039.8); // best (min) ask across the book
    expect(head.last).toBeCloseTo((1039.6 + 1039.8) / 2, 10); // depth MID
    expect(head.lastLabel).toBe("MID");
    // The negative L1 values never leak through.
    expect(head.bid).toBeGreaterThan(0);
    expect(head.last).toBeGreaterThan(0);
  });

  it("uses the relay nbbo summary for options when present", () => {
    const optionBook: DepthBook = {
      symbol: "CRCL_20260116_7_C",
      kind: "option",
      isSmartDepth: true,
      feed: "OPRA BBO",
      entitled: true,
      timestamp: "t",
      nbbo: { bestBid: 7.1, bestAsk: 7.55, mid: 7.325, bidSize: 40, askSize: 30 },
      bid: [{ price: 7.1, size: 40, marketMaker: null, exchange: "CBOE", nbbo: true }],
      ask: [{ price: 7.55, size: 30, marketMaker: null, exchange: "PHLX", nbbo: true }],
    };
    const head = deriveBookHeader(optionBook, CORRUPT_L1);
    expect(head.bid).toBe(7.1);
    expect(head.ask).toBe(7.55);
    expect(head.last).toBeCloseTo(7.325, 10);
    expect(head.lastLabel).toBe("MID");
  });

  it("keys the inside on index 0 for futures", () => {
    const futureBook: DepthBook = {
      symbol: "ESM6",
      kind: "future",
      isSmartDepth: false,
      feed: "CME DEPTH",
      entitled: true,
      timestamp: "t",
      bid: [
        { price: 5012.5, size: 1, marketMaker: null, exchange: null },
        { price: 5013.0, size: 1, marketMaker: null, exchange: null }, // not inside despite higher
      ],
      ask: [
        { price: 5012.75, size: 1, marketMaker: null, exchange: null },
        { price: 5012.0, size: 1, marketMaker: null, exchange: null }, // not inside despite lower
      ],
    };
    const head = deriveBookHeader(futureBook, CORRUPT_L1);
    expect(head.bid).toBe(5012.5); // index 0 inside, not max
    expect(head.ask).toBe(5012.75); // index 0 inside, not min
  });

  it("falls back to the L1 scalars when no entitled depth book is present", () => {
    const l1 = { bid: 7.1, ask: 7.55, last: 7.3, lastLabel: "LAST" };
    expect(deriveBookHeader(null, l1)).toEqual(l1);
    const unentitled: DepthBook = { ...STOCK_BOOK, entitled: false, bid: [], ask: [] };
    expect(deriveBookHeader(unentitled, l1)).toEqual(l1);
  });

  it("guards an empty side without throwing", () => {
    const oneSided: DepthBook = {
      ...STOCK_BOOK,
      ask: [],
    };
    const head = deriveBookHeader(oneSided, CORRUPT_L1);
    expect(head.bid).toBe(1039.6);
    expect(head.ask).toBeNull();
    expect(head.last).toBeNull(); // no mid without both sides
  });

  it("does not treat mixed overnight SMART depth as a negative spread", () => {
    // GLD after 20:00 ET: leftover ARCA extended-hours bids sit above live
    // IBEOS overnight asks. Max-bid/min-ask across all MPIDs is crossed;
    // the tradeable overnight book is IBEOS/OVERNIGHT.
    const gld: DepthBook = {
      symbol: "GLD",
      kind: "stock",
      isSmartDepth: true,
      feed: "SMART DEPTH",
      entitled: true,
      timestamp: "t",
      bid: [
        { price: 413.51, size: 84, marketMaker: "ARCA", exchange: "SMART" },
        { price: 413.21, size: 64, marketMaker: "ARCA", exchange: "SMART" },
        { price: 412.07, size: 280, marketMaker: "IBEOS", exchange: "SMART" },
        { price: 412.07, size: 620, marketMaker: "OVERNIGHT", exchange: "SMART" },
      ],
      ask: [
        { price: 412.24, size: 380, marketMaker: "IBEOS", exchange: "SMART" },
        { price: 412.24, size: 700, marketMaker: "OVERNIGHT", exchange: "SMART" },
        { price: 414.18, size: 46, marketMaker: "ARCA", exchange: "SMART" },
      ],
    };
    const l1 = { bid: -1, ask: -1, last: 412.88, lastLabel: "LAST" };
    const head = deriveBookHeader(gld, l1);
    expect(head.bid).toBe(412.07);
    expect(head.ask).toBe(412.24);
    expect(head.ask).toBeGreaterThanOrEqual(head.bid as number);
    expect(head.last).toBeCloseTo((412.07 + 412.24) / 2, 10);
    expect(head.lastLabel).toBe("MID");
  });

  it("falls back to a valid L1 BBO when SMART depth is crossed and overnight rows are missing", () => {
    const crossed: DepthBook = {
      ...STOCK_BOOK,
      bid: [{ price: 100, size: 1, marketMaker: "ARCA", exchange: "SMART" }],
      ask: [{ price: 99, size: 1, marketMaker: "BATS", exchange: "SMART" }],
    };
    const l1 = { bid: 99.9, ask: 100.1, last: 100, lastLabel: "LAST" };
    const head = deriveBookHeader(crossed, l1);
    expect(head.bid).toBe(99.9);
    expect(head.ask).toBe(100.1);
    expect(head.last).toBeCloseTo(100, 10);
  });
});

describe("parseOptionKey (BUG 3 — round-trips with optionKey)", () => {
  it("decomposes a composite option key into its contract", () => {
    expect(parseOptionKey("CRCL_20260116_7_C")).toEqual({
      symbol: "CRCL",
      expiry: "20260116",
      strike: 7,
      right: "C",
    });
  });

  it("round-trips optionKey -> parseOptionKey", () => {
    const c = { symbol: "RKLB", expiry: "20260620", strike: 150, right: "C" as const };
    expect(parseOptionKey(optionKey(c))).toEqual(c);
  });

  it("returns null for a bare stock ticker or futures root", () => {
    expect(parseOptionKey("MU")).toBeNull();
    expect(parseOptionKey("ES")).toBeNull();
  });

  it("returns null when the right segment is not C/P", () => {
    expect(parseOptionKey("CRCL_20260116_7_X")).toBeNull();
  });
});

describe("isBestLevel", () => {
  it("treats index 0 as best for stocks", () => {
    expect(isBestLevel(STOCK_BID[0], 0, "stock")).toBe(true);
    expect(isBestLevel(STOCK_BID[1], 1, "stock")).toBe(false);
  });

  it("treats index 0 as best for futures", () => {
    expect(isBestLevel(FUTURES_BID[0], 0, "future")).toBe(true);
    expect(isBestLevel(FUTURES_BID[2], 2, "future")).toBe(false);
  });

  it("uses the nbbo flag (not index) for options", () => {
    const nbboRow = { ...STOCK_BID[2], nbbo: true };
    const nonNbboRow = { ...STOCK_BID[0], nbbo: false };
    expect(isBestLevel(nbboRow, 2, "option")).toBe(true);
    expect(isBestLevel(nonNbboRow, 0, "option")).toBe(false);
  });

  it("is false for an option row without an nbbo flag", () => {
    expect(isBestLevel(STOCK_BID[0], 0, "option")).toBe(false);
  });
});
