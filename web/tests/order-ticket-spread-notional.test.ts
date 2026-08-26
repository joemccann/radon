import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { OpenOrder, PortfolioLeg } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import InstrumentDetailModal from "../components/InstrumentDetailModal";
import ModifyOrderModal from "../components/ModifyOrderModal";

vi.mock("../components/Modal", () => ({
  default: ({
    children,
    className,
  }: {
    children: React.ReactNode;
    className?: string;
  }) => React.createElement("div", { className: className ?? "mock-modal" }, children),
}));

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

const shortCallLeg: PortfolioLeg = {
  direction: "SHORT",
  contracts: 25,
  type: "Call",
  strike: 130,
  entry_cost: -20_015,
  avg_cost: -801,
  market_price: 3.9,
  market_price_is_calculated: false,
  market_value: 10_257,
};

const optionPrices: Record<string, PriceData> = {
  AAOI_20260320_130_C: makePriceData({
    symbol: "AAOI_20260320_130_C",
    bid: 3.3,
    ask: 4.5,
    last: 3.9,
    close: 10.05,
    volume: 46,
    high: 5.5,
    low: 3.6,
  }),
};

const openOrder: OpenOrder = {
  orderId: 101,
  permId: 202,
  symbol: "AAOI",
  contract: {
    conId: 123456,
    symbol: "AAOI",
    secType: "OPT",
    strike: 130,
    right: "C",
    expiry: "2026-03-20",
  },
  action: "BUY",
  orderType: "LMT",
  totalQuantity: 25,
  limitPrice: 3.9,
  auxPrice: null,
  status: "Submitted",
  filled: 0,
  remaining: 25,
  avgFillPrice: null,
  tif: "GTC",
};

describe("order-ticket spread telemetry", () => {
  it("uses raw spread dollars and percent in the single-leg instrument ticket", () => {
    const html = renderToStaticMarkup(
      React.createElement(InstrumentDetailModal, {
        leg: shortCallLeg,
        ticker: "AAOI",
        expiry: "2026-03-20",
        prices: optionPrices,
        onClose: () => {},
      }),
    );

    // InstrumentDetailModal shows raw market spread (no resting limit overlay)
    // bid=3.3, ask=4.5, spread=1.2, mid=3.9, pct=30.77%
    expect(html).toContain("$1.20 / 30.77%");
  });

  it("shows the market in the panel and the resting-limit overlay on the quick buttons", () => {
    // Was "applies resting limit overlay in the modify-order modal", which
    // asserted the DOCTORED spread ($0.60 / 14.29%) in the nine-field panel —
    // i.e. it pinned R-255. Order is BUY at limit $3.9 against a market of
    // bid $3.30 / ask $4.50: applyRestingLimitToQuote raises bid to 3.90, so
    // the panel was reporting a spread measured against the operator's own
    // order, with full market weight, captioned with the order's symbol.
    //
    // The overlay is still applied where it is meaningful — the reference
    // quick-fill buttons, whose job is "what can I actually get" — which is
    // the half of this case that was always right.
    const html = renderToStaticMarkup(
      React.createElement(ModifyOrderModal, {
        order: openOrder,
        loading: false,
        prices: optionPrices,
        portfolio: null,
        onConfirm: () => {},
        onClose: () => {},
      }),
    );

    // Panel: the true market, same figures the instrument ticket shows.
    expect(html).toContain("$1.20 / 30.77%");
    expect(html).not.toContain("$0.60 / 14.29%");
    // Quick buttons: still overlaid (bid raised to the resting 3.90, mid 4.20).
    expect(html).toContain("BID 3.90");
    expect(html).toContain("MID 4.20");
  });
});
