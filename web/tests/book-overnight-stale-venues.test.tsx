/** @vitest-environment jsdom */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import BookTab from "@/components/ticker-detail/BookTab";
import { fmtDepthPrice } from "@/components/ticker-detail/depthFormat";
import { deriveBookHeader, liveStockBook } from "@/lib/book/depthDerivations";
import { deriveMicrostructure } from "@/lib/book/microstructure";
import type { DepthBook, DepthLevel, PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));

afterEach(cleanup);

const timestamp = "2026-11-12T02:30:00.000Z";
const L1 = { bid: 163.14, ask: 163.17, bidSize: 17, askSize: 200 };

function arca(price: number, size: number): DepthLevel {
  return { price, size, marketMaker: null, exchange: "ARCA" };
}

function cents(from: number, count: number, step: number): number[] {
  return Array.from({ length: count }, (_, i) => Math.round((from + i * step) * 100) / 100);
}

/**
 * NVDA, Wed 2026-11-11 ~21:30 ET, SMART depth during the overnight session.
 * IB keeps ARCA's frozen regular-session book (~158.8) in the SMART aggregate.
 * Asks sort ascending, so 40 stale ARCA asks fill the whole 40-row budget and
 * push every live IBEOS/OVERNIGHT ask out of the ladder.
 */
function nvdaOvernightBook(): DepthBook {
  return {
    symbol: "NVDA",
    kind: "stock",
    entitled: true,
    isSmartDepth: true,
    feed: "SMART DEPTH",
    timestamp,
    bid: [
      { price: 163.14, size: 17, marketMaker: null, exchange: "IBEOS" },
      { price: 163.1, size: 118, marketMaker: null, exchange: "OVERNIGHT" },
      ...cents(158.75, 25, -0.01).map((p) => arca(p, 300)),
    ],
    ask: [
      arca(158.8, 13292),
      arca(158.82, 10029),
      ...cents(158.83, 38, 0.01).map((p) => arca(p, 400)),
    ],
  };
}

function nvdaPrice(): PriceData {
  return {
    symbol: "NVDA",
    last: 163.17,
    lastIsCalculated: false,
    bid: L1.bid,
    ask: L1.ask,
    bidSize: L1.bidSize,
    askSize: L1.askSize,
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
    timestamp,
  };
}

function bestBid(book: DepthBook): number {
  return Math.max(...book.bid.map((l) => l.price));
}

function bestAsk(book: DepthBook): number {
  return Math.min(...book.ask.map((l) => l.price));
}

describe("liveStockBook drops a stale session's venues from a crossed SMART book", () => {
  it("keeps the live overnight rows and drops every frozen ARCA row", () => {
    const live = liveStockBook(nvdaOvernightBook(), L1);
    const venues = [...live.bid, ...live.ask].map((l) => l.marketMaker ?? l.exchange);
    expect(venues).not.toContain("ARCA");
    expect(live.bid.map((l) => l.price)).toEqual([163.14, 163.1]);
    expect(bestAsk(live)).toBeGreaterThanOrEqual(bestBid(live));
  });

  it("seeds the side the stale venues crowded out with the live L1 quote", () => {
    const live = liveStockBook(nvdaOvernightBook(), L1);
    expect(live.ask).toEqual([{ price: 163.17, size: 200, marketMaker: null, exchange: "L1" }]);
  });

  it("drops stale overnight rows that cross live lit venues before the open", () => {
    const premarket: DepthBook = {
      ...nvdaOvernightBook(),
      bid: [
        { price: 155, size: 50, marketMaker: null, exchange: "IBEOS" },
        arca(150.1, 300),
      ],
      ask: [arca(150.12, 200), arca(150.15, 100)],
    };
    const live = liveStockBook(premarket, { bid: 150.1, ask: 150.12, bidSize: 300, askSize: 200 });
    expect(live.bid.map((l) => l.price)).toEqual([150.1]);
    expect(live.ask.map((l) => l.price)).toEqual([150.12, 150.15]);
  });

  it("leaves an uncrossed book untouched", () => {
    const book: DepthBook = {
      ...nvdaOvernightBook(),
      bid: [arca(100, 1), arca(99.99, 2)],
      ask: [arca(100.01, 3)],
    };
    expect(liveStockBook(book, { bid: 100, ask: 100.01, bidSize: 1, askSize: 3 })).toBe(book);
  });
});

describe("NVDA overnight book derivations read the live book", () => {
  it("header quotes the live overnight touch, not ARCA's frozen book", () => {
    const head = deriveBookHeader(nvdaOvernightBook(), {
      bid: L1.bid,
      ask: L1.ask,
      last: 163.17,
      lastLabel: "LAST",
    });
    expect(head.bid).toBe(163.14);
    expect(head.ask).toBe(163.17);
  });

  it("imbalance and microprice derive from the uncrossed book", () => {
    const micro = deriveMicrostructure(liveStockBook(nvdaOvernightBook(), L1));
    const bidSize = 17 + 118;
    const askSize = 200;
    expect(micro?.imbalance).toBeCloseTo((bidSize - askSize) / (bidSize + askSize), 10);
    expect(micro?.microprice).toBeCloseTo((163.14 * 200 + 163.17 * 17) / (200 + 17), 10);
    expect(micro?.microprice).toBeGreaterThanOrEqual(163.14);
    expect(micro?.microprice).toBeLessThanOrEqual(163.17);
  });
});

describe("BookTab renders the NVDA overnight book uncrossed", () => {
  function renderNvda() {
    const price = nvdaPrice();
    render(
      <BookTab
        ticker="NVDA"
        position={null}
        prices={{ NVDA: price }}
        openOrders={[]}
        tickerPriceData={price}
        depths={{ NVDA: nvdaOvernightBook() }}
        tape={{}}
        bookKey="NVDA"
        bookKind="stock"
        bookOnly
      />,
    );
  }

  it("shows no frozen ARCA levels in the montage", () => {
    renderNvda();
    const montage = document.querySelector(".book-montage")?.textContent ?? "";
    expect(montage).not.toContain("ARCA");
    expect(montage).not.toContain("158.80");
    expect(montage).toContain("163.14");
    expect(montage).toContain("163.17");
  });

  it("shows pressure from the live book, not the frozen ARCA walls", () => {
    renderNvda();
    const strip = document.querySelector(".microstructure-strip")?.textContent ?? "";
    const imbalance = (17 + 118 - 200) / (17 + 118 + 200);
    expect(strip).toContain(imbalance.toFixed(2));
    expect(strip).toContain("163.14");
  });
});

describe("fmtDepthPrice prints whole-cent prices at two decimals", () => {
  it.each([
    [158.8, "158.80"],
    [158.83, "158.83"],
    [163.14, "163.14"],
    [163.155, "163.1550"],
  ])("%s -> %s", (price, expected) => {
    expect(fmtDepthPrice(price)).toBe(expected);
  });
});
