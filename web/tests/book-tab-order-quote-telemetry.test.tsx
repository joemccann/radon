/** @vitest-environment jsdom */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import BookTab from "@/components/ticker-detail/BookTab";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));

// The telemetry model only treats a quote as live within 5 minutes of now, so
// the fixture timestamp must be resolved at call time, never hardcoded.
function stockPrice(overrides: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "NVDA",
    last: 182.5,
    lastIsCalculated: false,
    bid: 182.4,
    ask: 182.6,
    bidSize: 400,
    askSize: 300,
    volume: 12_500_000,
    high: 184.2,
    low: 180.1,
    open: 181,
    close: 180.5,
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

function telemetryLabels(): string[] {
  const panel = document.querySelector(".price-bar");
  if (!panel) return [];
  return [...panel.querySelectorAll(".price-bar-label")].map((n) => n.textContent ?? "");
}

afterEach(cleanup);

describe("BookTab stock ticket quote telemetry", () => {
  it("renders the full nine-field telemetry block above the stock order ticket", () => {
    const price = stockPrice();
    render(
      <BookTab
        ticker="NVDA"
        position={null}
        prices={{ NVDA: price }}
        openOrders={[]}
        tickerPriceData={price}
        depths={{}}
        tape={{}}
        bookKey="NVDA"
        bookKind="stock"
      />,
    );

    expect(telemetryLabels()).toEqual([
      "NVDA",
      "BID",
      "MID",
      "ASK",
      "SPREAD",
      "LAST",
      "VOLUME",
      "HIGH",
      "LOW",
      "DAY",
    ]);
    expect(document.querySelector(".price-bar--tight")).toBeTruthy();
    // The telemetry sits inside the ticket, above the Action field.
    expect(document.querySelector(".order-form .price-bar")).toBeTruthy();
  });

  it("labels a calculated last as MARK", () => {
    const price = stockPrice({ lastIsCalculated: true });
    render(
      <BookTab
        ticker="NVDA"
        position={null}
        prices={{ NVDA: price }}
        openOrders={[]}
        tickerPriceData={price}
        depths={{}}
        tape={{}}
        bookKey="NVDA"
        bookKind="stock"
      />,
    );
    expect(telemetryLabels()).toContain("MARK");
  });

  it("keeps the ticket's tap-to-fill BID / MID / ASK quick buttons", () => {
    const price = stockPrice();
    render(
      <BookTab
        ticker="NVDA"
        position={null}
        prices={{ NVDA: price }}
        openOrders={[]}
        tickerPriceData={price}
        depths={{}}
        tape={{}}
        bookKey="NVDA"
        bookKind="stock"
      />,
    );
    const quick = [...document.querySelectorAll(".btn-quick")].map((b) => b.textContent ?? "");
    expect(quick).toEqual(expect.arrayContaining(["BID", "MID", "ASK"]));
  });

  it("uses the underlying stock quote, not the focused option leg quote", () => {
    const stock = stockPrice();
    const optionQuote = stockPrice({
      symbol: "NVDA 20260918 C 200",
      bid: 4.1,
      ask: 4.3,
      last: 4.2,
      high: 4.9,
      low: 3.8,
      volume: 1200,
      close: 4,
    });
    render(
      <BookTab
        ticker="NVDA"
        position={null}
        prices={{ NVDA: stock }}
        openOrders={[]}
        tickerPriceData={optionQuote}
        depths={{}}
        tape={{}}
        bookKey="NVDA 20260918 C 200"
        bookKind="option"
      />,
    );
    const values = [...document.querySelectorAll(".price-bar .price-bar-value")].map(
      (n) => n.textContent ?? "",
    );
    expect(values.join(" ")).toContain("182.40");
    expect(values.join(" ")).not.toContain("4.10");
  });
});
