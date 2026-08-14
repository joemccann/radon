/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import BookTab from "@/components/ticker-detail/BookTab";
import type { DepthBook, PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));

const timestamp = "2026-08-14T16:30:03.000Z";

function cbrsPrice(): PriceData {
  return {
    symbol: "CBRS",
    last: 225,
    lastIsCalculated: false,
    bid: 225,
    ask: 225.4,
    bidSize: 700,
    askSize: 500,
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

function entitledL2(): DepthBook {
  return {
    symbol: "CBRS",
    kind: "stock",
    entitled: true,
    isSmartDepth: true,
    feed: "SMART DEPTH",
    timestamp,
    bid: [
      { price: 225, size: 700, marketMaker: null, exchange: "NSDQ" },
      { price: 224.9, size: 200, marketMaker: null, exchange: "ARCA" },
    ],
    ask: [
      { price: 225.4, size: 500, marketMaker: null, exchange: "NSDQ" },
      { price: 225.5, size: 100, marketMaker: null, exchange: "BATS" },
    ],
  };
}

afterEach(cleanup);

describe("BookTab stock book when L2 is absent", () => {
  it("renders a montage from L1 BBO instead of an empty L1 panel", () => {
    const price = cbrsPrice();
    render(
      <BookTab
        ticker="CBRS"
        position={null}
        prices={{ CBRS: price }}
        openOrders={[]}
        tickerPriceData={price}
        depths={{}}
        tape={{}}
        bookKey="CBRS"
        bookKind="stock"
        bookOnly
      />,
    );

    expect(document.querySelector(".book-sides")).toBeTruthy();
    expect(document.querySelector(".book-l1")).toBeNull();
    expect(screen.getByText("L1 BBO")).toBeTruthy();
    const montage = document.querySelector(".book-montage")?.textContent ?? "";
    expect(montage).toContain("225.00");
    expect(montage).toContain("225.40");
    expect(montage).toContain("700");
    expect(montage).toContain("500");
  });

  it("keeps entitled L2 when the relay has a live book", () => {
    const price = cbrsPrice();
    render(
      <BookTab
        ticker="CBRS"
        position={null}
        prices={{ CBRS: price }}
        openOrders={[]}
        tickerPriceData={price}
        depths={{ CBRS: entitledL2() }}
        tape={{}}
        bookKey="CBRS"
        bookKind="stock"
        bookOnly
      />,
    );

    expect(screen.getByText("SMART DEPTH")).toBeTruthy();
    expect(screen.queryByText("L1 BBO")).toBeNull();
    expect(document.querySelectorAll(".book-side.bid .book-row")).toHaveLength(2);
  });
});
