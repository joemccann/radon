/**
 * @vitest-environment jsdom
 *
 * ModifyOrderModal renders the same nine-field quote telemetry the portfolio
 * position drawer gives the operator: BID MID ASK / SPREAD LAST VOLUME /
 * HIGH LOW DAY.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { OpenOrder } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import ModifyOrderModal from "@/components/ModifyOrderModal";

vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0,
}));

vi.mock("@/components/Modal", () => ({
  default: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? React.createElement("div", { className: "mock-modal" }, children) : null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const NINE_LABELS = ["BID", "MID", "ASK", "SPREAD", "LAST", "VOLUME", "HIGH", "LOW", "DAY"];

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

const OPTION_KEY = optionKey({ symbol: "SNDK", expiry: "20260717", strike: 1570, right: "C" });

function optionOrder(): OpenOrder {
  return {
    orderId: 95,
    permId: 653624857,
    symbol: "SNDK C1570",
    contract: {
      conId: 987654,
      symbol: "SNDK",
      secType: "OPT",
      strike: 1570,
      right: "C",
      expiry: "2026-07-17",
    },
    action: "SELL",
    orderType: "LMT",
    totalQuantity: 4,
    limitPrice: 100,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 4,
    avgFillPrice: null,
    tif: "DAY",
  };
}

function optionPrices(): Record<string, PriceData> {
  return {
    [OPTION_KEY]: makePriceData({
      symbol: OPTION_KEY,
      bid: 93.5,
      ask: 96.5,
      last: 95,
      close: 90,
      volume: 812,
      high: 99.4,
      low: 88.2,
    }),
  };
}

function renderModal(order: OpenOrder, prices?: Record<string, PriceData>) {
  return render(
    <ModifyOrderModal
      order={order}
      loading={false}
      prices={prices}
      onConfirm={vi.fn()}
      onClose={() => {}}
    />,
  );
}

function telemetryLabels(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".price-bar-label")].map((node) => node.textContent ?? "");
}

describe("ModifyOrderModal quote telemetry", () => {
  it("renders the full nine-field telemetry for the order being modified", () => {
    const { container } = renderModal(optionOrder(), optionPrices());
    const labels = telemetryLabels(container);
    for (const label of NINE_LABELS) {
      expect(labels).toContain(label);
    }
    expect(labels[0]).toBe("SNDK C1570");
  });

  it("uses the tight density inside the modify primary panel", () => {
    const { container } = renderModal(optionOrder(), optionPrices());
    const bar = container.querySelector(".price-bar");
    expect(bar?.classList.contains("price-bar--tight")).toBe(true);
  });

  it("keeps the BID/MID/ASK reference buttons that fill the limit price", () => {
    renderModal(optionOrder(), optionPrices());
    expect(screen.getByRole("button", { name: /^BID/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^MID/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^ASK/ })).toBeTruthy();
  });

  it("renders no duplicate informational price strip", () => {
    const { container } = renderModal(optionOrder(), optionPrices());
    expect(container.querySelector('[data-testid="order-price-strip"]')).toBeNull();
  });

  it("falls back to the empty telemetry state when no quote is reachable", () => {
    const { container } = renderModal(optionOrder(), undefined);
    expect(container.querySelector(".price-bar-empty")).toBeTruthy();
    expect(container.querySelectorAll(".price-bar-label")).toHaveLength(0);
  });
});
