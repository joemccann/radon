// @vitest-environment jsdom

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { OrderBook } from "../components/ticker-detail/OrderBook";
import { TimeAndSales } from "../components/ticker-detail/TimeAndSales";
import { isFuturesRoot, FUTURES_ROOTS } from "../lib/futuresSymbols";
import type { DepthBook, Trade } from "../lib/pricesProtocol";

// next/navigation is pulled in transitively by some sibling components; mock it
// so the jsdom render never reaches for a real router.
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/RKLB",
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

/* ── fixtures ── */

const STOCK_BOOK: DepthBook = {
  symbol: "RKLB",
  kind: "stock",
  isSmartDepth: true,
  feed: "SMART DEPTH · TOTALVIEW",
  entitled: true,
  timestamp: "2026-05-31T16:00:00Z",
  bid: [
    { price: 152.52, size: 34, marketMaker: "XNMS", exchange: "SMART" },
    { price: 152.52, size: 100, marketMaker: "IEXG", exchange: "SMART" },
    { price: 152.51, size: 100, marketMaker: "XNYS", exchange: "SMART" },
  ],
  ask: [
    { price: 152.7, size: 738, marketMaker: "XNMS", exchange: "SMART" },
    { price: 152.7, size: 100, marketMaker: "BATY", exchange: "SMART" },
    { price: 152.75, size: 10, marketMaker: "XNMS", exchange: "SMART" },
  ],
};

const OPTION_BOOK: DepthBook = {
  symbol: "RKLB_20260620_150_C",
  kind: "option",
  isSmartDepth: true,
  feed: "OPRA · PER-EXCHANGE BBO",
  entitled: true,
  timestamp: "2026-05-31T16:00:00Z",
  bid: [
    { price: 4.35, size: 42, marketMaker: null, exchange: "CBOE", nbbo: true },
    { price: 4.35, size: 18, marketMaker: null, exchange: "EDGX", nbbo: true },
    { price: 4.3, size: 25, marketMaker: null, exchange: "PHLX" },
  ],
  ask: [
    { price: 4.45, size: 30, marketMaker: null, exchange: "PHLX", nbbo: true },
    { price: 4.5, size: 55, marketMaker: null, exchange: "CBOE" },
  ],
};

const FUTURE_BOOK: DepthBook = {
  symbol: "ESM6",
  kind: "future",
  isSmartDepth: false,
  feed: "CME · GLOBEX DEPTH",
  entitled: true,
  timestamp: "2026-05-31T16:00:00Z",
  bid: [
    { price: 5012.5, size: 137, marketMaker: null, exchange: null },
    { price: 5012.25, size: 295, marketMaker: null, exchange: null },
  ],
  ask: [
    { price: 5012.75, size: 142, marketMaker: null, exchange: null },
    { price: 5013.0, size: 318, marketMaker: null, exchange: null },
  ],
};

// OLDEST-first, matching the relay ring-buffer snapshot contract. Times are
// epoch-seconds strings (what reqTickByTickData emits). Oldest -> newest:
// 152.70 then 152.68 (down) then 152.73 (up).
const TAPE: Trade[] = [
  { price: 152.7, size: 1, exchange: "FINY", time: "1780000027" },
  { price: 152.68, size: 4, exchange: "FINY", time: "1780000028" },
  { price: 152.73, size: 9, exchange: "XNYS", time: "1780000029" },
];

const L1_FALLBACK = <div data-testid="l1-fallback">L1 FALLBACK</div>;

function renderBook(
  book: DepthBook | null,
  kind: DepthBook["kind"],
  l1?: { bid: number | null; ask: number | null; last: number | null; lastLabel?: string },
) {
  return render(
    <OrderBook
      symbolLabel={book?.symbol ?? "RKLB"}
      kind={kind}
      depth={book}
      trades={TAPE}
      last={l1 ? l1.last : (book?.bid[0]?.price ?? null)}
      lastLabel={l1?.lastLabel}
      bid={l1 ? l1.bid : (book?.bid[0]?.price ?? null)}
      ask={l1 ? l1.ask : (book?.ask[0]?.price ?? null)}
      l1Fallback={L1_FALLBACK}
    />,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("OrderBook render — all three kinds", () => {
  it("renders the stock two-sided montage", () => {
    const { container } = renderBook(STOCK_BOOK, "stock");
    expect(container.querySelector(".book-sides")).toBeTruthy();
    // Stock montage draws the price-level edge marker on the first row of a level.
    expect(container.querySelector('.book-row[data-lvlfirst="1"]')).toBeTruthy();
    // Market-maker labels surface.
    expect(container.textContent).toContain("XNMS");
    expect(container.querySelector(".book-ladder")).toBeNull();
  });

  it("renders the option per-exchange BBO montage and suppresses the edge marker", () => {
    const { container } = renderBook(OPTION_BOOK, "option");
    expect(container.querySelector(".book-sides")).toBeTruthy();
    // Options suppress the price-level edge marker entirely.
    expect(container.querySelector('.book-row[data-lvlfirst="1"]')).toBeNull();
    // NBBO best-rule marks the flagged venues, not index 0 alone.
    expect(container.querySelectorAll(".book-row.best").length).toBeGreaterThan(0);
    expect(container.textContent).toContain("CBOE");
  });

  it("renders the NBBO marker on exactly the flagged option rows", () => {
    const { container } = renderBook(OPTION_BOOK, "option");
    // Three venue rows are flagged nbbo:true (2 bid ties @4.35 + 1 ask @4.45).
    const nbboRows = container.querySelectorAll(".book-row.nbbo");
    expect(nbboRows.length).toBe(3);
    // The explicit "NBBO" tag rides exactly the flagged rows — one per row.
    const tags = container.querySelectorAll(".book-nbbo-tag");
    expect(tags.length).toBe(3);
    // Every nbbo row also carries the .best inside-marker; every .best row is nbbo.
    expect(container.querySelectorAll(".book-row.best").length).toBe(3);
    // The outside (non-NBBO) venues carry neither the marker nor the tag.
    const allRows = [...container.querySelectorAll(".book-row")];
    const outsideRows = allRows.filter((r) => !r.classList.contains("nbbo"));
    expect(outsideRows.length).toBe(2); // bid @4.30 PHLX + ask @4.50 CBOE
    for (const row of outsideRows) {
      expect(row.classList.contains("best")).toBe(false);
      expect(row.querySelector(".book-nbbo-tag")).toBeNull();
    }
  });

  it("frames the option montage as per-exchange BBO, not stacked depth", () => {
    const { container } = renderBook(OPTION_BOOK, "option");
    const note = container.querySelector(".book-montage-note");
    expect(note).toBeTruthy();
    expect(note?.textContent).toContain("OPRA top of book");
    // Stock montage carries no such note.
    const { container: stock } = renderBook(STOCK_BOOK, "stock");
    expect(stock.querySelector(".book-montage-note")).toBeNull();
  });

  it("renders the futures centered ladder", () => {
    const { container } = renderBook(FUTURE_BOOK, "future");
    expect(container.querySelector(".book-ladder")).toBeTruthy();
    expect(container.querySelector(".book-ladder-spread")).toBeTruthy();
    expect(container.querySelector(".book-sides")).toBeNull();
    expect(container.textContent).toContain("5012.50");
  });
});

describe("Window head — derives from the depth book, not the L1 feed (BUG 1)", () => {
  it("shows the depth bid/ask/MID even when the passed-in L1 scalars are corrupt", () => {
    // MU corruption signature: negative MARK / BID on the L1 feed.
    const { container } = renderBook(STOCK_BOOK, "stock", {
      bid: -80,
      ask: -79,
      last: -80,
      lastLabel: "MARK",
    });
    const headText = container.querySelector(".book-window-head")?.textContent ?? "";
    // Best bid (max) = 152.52, best ask (min) = 152.70, MID = 152.61.
    expect(headText).toContain("152.52");
    expect(headText).toContain("152.70");
    expect(headText).toContain("MID");
    expect(headText).toContain("152.61");
    // The corrupt negative L1 values never leak into the head.
    expect(headText).not.toContain("-80");
    expect(headText).not.toContain("-79");
  });

  it("falls back to the L1 scalars in the head when depth is unentitled", () => {
    const unentitled: DepthBook = { ...STOCK_BOOK, entitled: false, bid: [], ask: [] };
    const { container } = renderBook(unentitled, "stock", {
      bid: 100.25,
      ask: 100.5,
      last: 100.4,
      lastLabel: "LAST",
    });
    const headText = container.querySelector(".book-window-head")?.textContent ?? "";
    expect(headText).toContain("100.25");
    expect(headText).toContain("100.50");
    expect(headText).toContain("LAST");
  });

  it("uses the option NBBO summary for the head", () => {
    const withNbbo: DepthBook = {
      ...OPTION_BOOK,
      nbbo: { bestBid: 4.35, bestAsk: 4.45, mid: 4.4, bidSize: 60, askSize: 30 },
    };
    const { container } = renderBook(withNbbo, "option", {
      bid: 7.1,
      ask: 7.55,
      last: 7.3,
      lastLabel: "MARK",
    });
    const headText = container.querySelector(".book-window-head")?.textContent ?? "";
    expect(headText).toContain("4.35");
    expect(headText).toContain("4.45");
    expect(headText).toContain("4.40"); // mid
    // The L1 fallback values are not shown when depth is entitled.
    expect(headText).not.toContain("7.10");
  });
});

describe("Time & Sales tape scroll region (BUG 2)", () => {
  it("wraps the tape rows in a dedicated internal-scroll region", () => {
    const { container } = renderBook(STOCK_BOOK, "stock");
    const scrollRegion = container.querySelector(".book-tape .book-tape-rows");
    expect(scrollRegion).toBeTruthy();
    // The trade rows live inside the scroll region (so only they scroll, the
    // colhead above stays pinned).
    expect(scrollRegion?.querySelectorAll(".book-trow").length).toBe(TAPE.length);
    // The colhead is a sibling of the scroll region, not inside it.
    const colhead = container.querySelector(".book-tape > .book-colhead");
    expect(colhead).toBeTruthy();
    expect(colhead?.querySelector(".book-tape-rows")).toBeNull();
  });
});

describe("Time & Sales toggle reflow", () => {
  it("flips the tape-hidden class and persists to localStorage", () => {
    const { container, getByRole } = renderBook(STOCK_BOOK, "stock");
    const grid = container.querySelector(".book-body-grid")!;
    expect(grid.classList.contains("tape-hidden")).toBe(false);

    const toggle = getByRole("switch");
    fireEvent.click(toggle);

    expect(grid.classList.contains("tape-hidden")).toBe(true);
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(window.localStorage.getItem("radon:book:tape")).toBe("off");

    fireEvent.click(toggle);
    expect(grid.classList.contains("tape-hidden")).toBe(false);
    expect(window.localStorage.getItem("radon:book:tape")).toBe("on");
  });
});

describe("Degradation — L1 fallback", () => {
  it("renders the L1 fallback when depth is null", () => {
    const { getByTestId } = renderBook(null, "stock");
    expect(getByTestId("l1-fallback")).toBeTruthy();
  });

  it("renders the L1 fallback when entitled:false", () => {
    const unentitled: DepthBook = { ...STOCK_BOOK, entitled: false, bid: [], ask: [] };
    const { getByTestId, container } = renderBook(unentitled, "stock");
    expect(getByTestId("l1-fallback")).toBeTruthy();
    expect(container.querySelector(".book-sides")).toBeNull();
  });
});

describe("Tape tick classification", () => {
  it("colors up / down / flat by the tick test", () => {
    const { container } = render(<TimeAndSales trades={TAPE} visible />);
    const tones = [...container.querySelectorAll(".book-t-px")].map((el) =>
      el.className.replace("book-t-px", "").trim(),
    );
    // Input is oldest-first → classifyTicks yields [flat, down, up]; the tape
    // renders NEWEST-at-top (reversed), so rows read [up, down, flat].
    expect(tones).toEqual(["up", "down", "flat"]);
  });
});

describe("Futures-root routing → LadderDOM", () => {
  it("treats the relay-supported futures roots as futures (pre-depth hint)", () => {
    for (const root of ["ES", "NQ", "RTY", "YM", "CL", "GC", "ZB", "ZN"]) {
      expect(isFuturesRoot(root)).toBe(true);
      expect(isFuturesRoot(root.toLowerCase())).toBe(true);
    }
    // VIX is reached through the index path (no reqMktDepth) — never a root.
    expect(isFuturesRoot("VIX")).toBe(false);
    expect(isFuturesRoot("AAPL")).toBe(false);
    expect(isFuturesRoot(null)).toBe(false);
    expect(FUTURES_ROOTS.has("ES")).toBe(true);
  });

  it("renders the centered ladder (not the montage) when a futures-root book streams", () => {
    // A futures root resolves kind="future"; with an entitled future DepthBook
    // the OrderBook dispatches to <LadderDOM>, never the two-sided montage.
    const { container } = render(
      <OrderBook
        symbolLabel="ES"
        kind="future"
        depth={{ ...FUTURE_BOOK, symbol: "ES" }}
        trades={[]}
        last={5012.5}
        bid={5012.5}
        ask={5012.75}
        l1Fallback={L1_FALLBACK}
      />,
    );
    expect(container.querySelector(".book-ladder")).toBeTruthy();
    expect(container.querySelector(".book-sides")).toBeNull();
    expect(container.textContent).toContain("5012.50");
  });
});

describe("Time & Sales tape wiring", () => {
  it("renders one row per trade with tick-test tone classes (newest at top)", () => {
    const { container } = renderBook(STOCK_BOOK, "stock");
    const rows = container.querySelectorAll(".book-trow");
    expect(rows.length).toBe(TAPE.length);
    const tones = [...container.querySelectorAll(".book-t-px")].map((el) =>
      el.className.replace("book-t-px", "").trim(),
    );
    // Oldest-first input tick-tests to [flat, down, up]; rendered reversed so
    // the newest print is at the top → [up, down, flat].
    expect(tones).toEqual(["up", "down", "flat"]);
    // Sizes + exchanges surface.
    expect(container.textContent).toContain("FINY");
  });

  it("shows the column header only when the tape is empty", () => {
    const { container } = render(<TimeAndSales trades={[]} visible />);
    // Header is always present.
    expect(container.querySelector(".book-colhead")).toBeTruthy();
    expect(container.textContent).toContain("Price");
    // No trade rows.
    expect(container.querySelectorAll(".book-trow").length).toBe(0);
  });

  it("still renders the empty tape inside the OrderBook (no crash, header only)", () => {
    const { container } = render(
      <OrderBook
        symbolLabel="RKLB"
        kind="stock"
        depth={STOCK_BOOK}
        trades={[]}
        last={152.52}
        bid={152.52}
        ask={152.7}
        l1Fallback={L1_FALLBACK}
      />,
    );
    expect(container.querySelector(".book-colhead")).toBeTruthy();
    expect(container.querySelectorAll(".book-trow").length).toBe(0);
  });
});

describe("Ask-side SHARES header sits toward the price, not the venue", () => {
  it("does not right-align the ask size header into MARKET", () => {
    const book: DepthBook = {
      ...STOCK_BOOK,
      ask: [
        { price: 105.24, size: 195, marketMaker: "DRCTEDARK", exchange: "SMART" },
        { price: 105.25, size: 105, marketMaker: "IEXG", exchange: "SMART" },
      ],
    };
    const { container } = renderBook(book, "stock");
    const askHead = [...container.querySelectorAll(".book-side.ask .book-colhead > span")];
    expect(askHead.map((el) => el.textContent)).toEqual(["Ask", "Shares", "Market"]);
    expect(askHead[1].classList.contains("r")).toBe(false);
    expect(askHead[2].classList.contains("r")).toBe(true);

    const askRow = container.querySelector(".book-side.ask .book-row")!;
    expect(askRow.querySelector(".book-shares")?.textContent).toBe("195");
    expect(askRow.querySelector(".book-mkt")?.textContent).toBe("DRCTEDARK");
  });
});

describe("Brand tokens only — no raw hex / tailwind color utilities", () => {
  it("emits no raw hex colors or green-500/red-500 in the rendered markup", () => {
    const { container } = renderBook(STOCK_BOOK, "stock");
    const html = container.innerHTML;
    // No inline raw hex colors.
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    // No tailwind non-brand color utilities.
    expect(html).not.toMatch(/\b(green|red)-500\b/);
  });
});
