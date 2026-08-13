import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import CockpitHeader from "../components/ticker-detail/CockpitHeader";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

// The cockpit header is the single source for the spread scalar (the legacy
// shared price bar was retired with the cockpit cutover). This pins the spread
// rendering to raw dollars + percent — never the wrong "$110.00 / 240 bps".

function makePriceData(overrides: Partial<PriceData> & { symbol: string }): PriceData {
  return {
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
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
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("CockpitHeader spread telemetry", () => {
  it("shows raw spread dollars and percent, never bps", () => {
    const quote = makePriceData({
      symbol: "AMD_20270115_195_C",
      bid: 45.3,
      ask: 46.4,
      last: 45.75,
      close: 48.95,
    });

    const html = renderToStaticMarkup(
      createElement(CockpitHeader, {
        ticker: "AMD",
        kind: "option",
        quotePriceData: quote,
        isSpreadNet: false,
        position: null,
        live: true,
        onDeckChange: () => {},
      }),
    );

    // ask - bid = 1.10; mid 45.85 → 2.40%. The value is wrapped in <b>, so assert
    // the parts rather than a contiguous string.
    expect(html).toContain("SPREAD");
    expect(html).toContain("$1.10");
    expect(html).toContain("2.40%");
    expect(html).not.toContain("$110.00");
    expect(html).not.toContain("240 bps");
  });
});
