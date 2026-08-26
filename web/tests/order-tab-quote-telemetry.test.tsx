/**
 * @vitest-environment jsdom
 *
 * The Order tab is an order-entry surface, so it must show the operator the
 * same nine-field quote telemetry the portfolio position drawer shows:
 * BID MID ASK / SPREAD LAST VOLUME / HIGH LOW DAY. Before this it rendered
 * only the four BID/MID/ASK quick-fill buttons, which are inputs and not
 * telemetry.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import OrderTab from "@/components/ticker-detail/OrderTab";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel: vi.fn(),
    requestModify: vi.fn(),
    pushNotification: vi.fn(),
    drainNotifications: vi.fn(() => []),
    setOrdersUpdater: vi.fn(),
  }),
  useOrderActionsOptional: () => ({ pushNotification: vi.fn() }),
}));

vi.mock("@/components/ModifyOrderModal", () => ({ default: () => null }));

const NINE_FIELDS = ["BID", "MID", "ASK", "SPREAD", "LAST", "VOLUME", "HIGH", "LOW", "DAY"];

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

const HELD_CALL: PortfolioPosition = {
  id: 7,
  ticker: "NVDA",
  structure: "Long Call $190.0",
  structure_type: "Long Call",
  risk_profile: "defined",
  expiry: "2026-09-18",
  contracts: 4,
  direction: "LONG",
  entry_cost: 2000,
  max_risk: 2000,
  market_value: 2400,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-07-01",
  legs: [
    {
      direction: "LONG",
      contracts: 4,
      type: "Call",
      strike: 190,
      entry_cost: 2000,
      avg_cost: 500,
      market_price: 6,
      market_value: 2400,
      market_price_is_calculated: false,
    },
  ],
};

function telemetryPanel(container: HTMLElement): HTMLElement | null {
  return container.querySelector(".order-form .price-bar");
}

function telemetryLabels(container: HTMLElement): string[] {
  const panel = telemetryPanel(container);
  if (!panel) return [];
  return [...panel.querySelectorAll(".price-bar-label")].map((n) => n.textContent ?? "");
}

function telemetryValues(container: HTMLElement): Record<string, string> {
  const panel = telemetryPanel(container);
  const values: Record<string, string> = {};
  panel?.querySelectorAll(".price-bar-item").forEach((row) => {
    const label = row.querySelector(".price-bar-label")?.textContent ?? "";
    const value = row.querySelector(".price-bar-value")?.textContent ?? "";
    if (label && value) values[label] = value;
  });
  return values;
}

afterEach(cleanup);

describe("OrderTab new-order quote telemetry", () => {
  it("renders the full nine-field telemetry block on a fresh stock order", () => {
    const price = stockPrice();
    const { container } = render(
      <OrderTab
        ticker="NVDA"
        position={null}
        portfolio={null}
        prices={{ NVDA: price }}
        openOrders={[]}
        tickerPriceData={price}
      />,
    );

    expect(telemetryLabels(container)).toEqual(["NVDA", ...NINE_FIELDS]);
    expect(telemetryValues(container)).toMatchObject({
      BID: "$182.40",
      MID: "$182.50",
      ASK: "$182.60",
      SPREAD: "$0.20 / 0.11%",
      LAST: "$182.50",
      VOLUME: "12,500,000",
      HIGH: "$184.20",
      LOW: "$180.10",
      DAY: "+1.11%",
    });
  });

  it("keeps the bid/mid/ask quick-fill buttons alongside the telemetry", () => {
    const price = stockPrice();
    const { container } = render(
      <OrderTab
        ticker="NVDA"
        position={null}
        portfolio={null}
        prices={{ NVDA: price }}
        openOrders={[]}
        tickerPriceData={price}
      />,
    );

    const quickFills = [...container.querySelectorAll(".modify-quick-buttons .btn-quick")].map(
      (n) => n.textContent ?? "",
    );
    expect(quickFills).toEqual(["BID", "MID", "ASK"]);
  });

  it("labels the option quote MARK when the close-position ticket has a calculated last", () => {
    const optionQuote = stockPrice({
      symbol: "NVDA_20260918_190_C",
      last: 6.1,
      lastIsCalculated: true,
      bid: 6,
      ask: 6.2,
      volume: 1200,
      high: 6.4,
      low: 5.7,
      close: 5.9,
    });
    const { container } = render(
      <OrderTab
        ticker="NVDA"
        position={HELD_CALL}
        portfolio={null}
        prices={{ NVDA: stockPrice(), NVDA_20260918_190_C: optionQuote }}
        openOrders={[]}
        tickerPriceData={optionQuote}
      />,
    );

    expect(telemetryLabels(container)).toEqual([
      "NVDA",
      "BID",
      "MID",
      "ASK",
      "SPREAD",
      "MARK",
      "VOLUME",
      "HIGH",
      "LOW",
      "DAY",
    ]);
    expect(telemetryValues(container).MARK).toBe("$6.10");
  });

  it("renders the honest empty state when no quote has arrived", () => {
    const { container } = render(
      <OrderTab
        ticker="NVDA"
        position={null}
        portfolio={null}
        prices={{}}
        openOrders={[]}
        tickerPriceData={null}
      />,
    );

    expect(container.querySelectorAll(".price-bar-label")).toHaveLength(0);
    expect(container.querySelector(".order-form .price-bar-empty")?.textContent).toBe("No real-time data");
  });
});

const HELD_SPREAD: PortfolioPosition = {
  id: 8,
  ticker: "NVDA",
  structure: "Bull Call Spread $190.0/$200.0",
  structure_type: "Bull Call Spread",
  risk_profile: "defined",
  expiry: "2026-09-18",
  contracts: 4,
  direction: "COMBO",
  entry_cost: 1600,
  max_risk: 1600,
  market_value: 2000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-07-01",
  legs: [
    {
      direction: "LONG", contracts: 4, type: "Call", strike: 190,
      entry_cost: 2400, avg_cost: 600, market_price: 7, market_value: 2800,
      market_price_is_calculated: false,
    },
    {
      direction: "SHORT", contracts: 4, type: "Call", strike: 200,
      entry_cost: 800, avg_cost: 200, market_price: 2, market_value: 800,
      market_price_is_calculated: false,
    },
  ],
};

function legPrice(symbol: string, bid: number, ask: number): PriceData {
  return { ...stockPrice(), symbol, bid, ask, last: (bid + ask) / 2, volume: null, high: null, low: null, open: null, close: null };
}

/**
 * The combo branch of the Order tab is its own order-entry surface ("Place
 * Combo Order") and was shipped without the telemetry block that the
 * single-leg branch got - it showed only its three-field net BID/MID/ASK
 * strip. Caught on production after the first rollout.
 */
describe("OrderTab combo quote telemetry", () => {
  it("renders the nine-field telemetry block on the combo order form", () => {
    const prices = {
      NVDA: stockPrice(),
      NVDA_20260918_190_C: legPrice("NVDA_20260918_190_C", 7.0, 7.4),
      NVDA_20260918_200_C: legPrice("NVDA_20260918_200_C", 2.0, 2.2),
    };
    const { container } = render(
      <OrderTab
        ticker="NVDA"
        position={HELD_SPREAD}
        portfolio={null}
        prices={prices}
        openOrders={[]}
        tickerPriceData={prices.NVDA}
      />,
    );

    expect(telemetryLabels(container)).toEqual(
      expect.arrayContaining(["BID", "MID", "ASK", "SPREAD", "VOLUME", "HIGH", "LOW", "DAY"]),
    );
    const values = telemetryValues(container);
    // Natural spread: BUY leg pays ask, SELL leg receives bid -> 7.4 - 2.0 = 5.40 ask,
    // 7.0 - 2.2 = 4.80 bid. A combo has no session OHLV, so those read "---".
    expect(values.BID).toBe("$4.80");
    expect(values.ASK).toBe("$5.40");
    expect(values.VOLUME).toBe("---");
    expect(values.HIGH).toBe("---");
    expect(values.DAY).toBe("---");
    // MARK, not LAST: the net is calculated, never a printed trade.
    expect(values.MARK).toBe("$5.10");
  });

  it("keeps the combo net BID/MID/ASK quick-fill buttons", () => {
    const prices = {
      NVDA: stockPrice(),
      NVDA_20260918_190_C: legPrice("NVDA_20260918_190_C", 7.0, 7.4),
      NVDA_20260918_200_C: legPrice("NVDA_20260918_200_C", 2.0, 2.2),
    };
    const { container } = render(
      <OrderTab
        ticker="NVDA"
        position={HELD_SPREAD}
        portfolio={null}
        prices={prices}
        openOrders={[]}
        tickerPriceData={prices.NVDA}
      />,
    );
    const quick = [...container.querySelectorAll("button.btn-quick")].map((b) => b.textContent ?? "");
    expect(quick.some((t) => t.startsWith("BID"))).toBe(true);
    expect(quick.some((t) => t.startsWith("MID"))).toBe(true);
    expect(quick.some((t) => t.startsWith("ASK"))).toBe(true);
  });
});
