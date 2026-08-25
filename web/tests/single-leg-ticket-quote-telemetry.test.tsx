/**
 * @vitest-environment jsdom
 *
 * SingleLegOrderTicket renders the shared nine-field quote telemetry block so
 * the operator entering an order sees the same information the portfolio
 * position drawer gives them.
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import SingleLegOrderTicket from "../components/SingleLegOrderTicket";
import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

afterEach(cleanup);

const emptyPortfolio = { positions: [], account_summary: null } as unknown as PortfolioData;

const NINE_LABELS = ["BID", "MID", "ASK", "SPREAD", "LAST", "VOLUME", "HIGH", "LOW", "DAY"];

const LIVE_QUOTE: PriceData = {
  symbol: "AAPL",
  last: 171.5,
  lastIsCalculated: false,
  bid: 171.0,
  ask: 172.0,
  bidSize: 3,
  askSize: 5,
  volume: 41_230_000,
  high: 173.4,
  low: 169.8,
  open: 170.2,
  close: 168.9,
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
} as unknown as PriceData;

function renderTicket(
  overrides: Partial<React.ComponentProps<typeof SingleLegOrderTicket>> = {},
) {
  const props: React.ComponentProps<typeof SingleLegOrderTicket> = {
    defaultAction: "BUY",
    defaultTif: "DAY",
    quantity: "100",
    onQuantityChange: () => {},
    quantityPlaceholder: "Shares",
    bid: 171,
    mid: 171.5,
    ask: 172,
    isValid: true,
    limitPrice: "171.50",
    onLimitPriceChange: () => {},
    riskInput: null,
    portfolio: emptyPortfolio,
    riskSurface: "single-leg-telemetry-test",
    buildPayload: () => ({}),
    buildSuccessMessage: () => "ok",
    ...overrides,
  };
  return render(<SingleLegOrderTicket {...props} />);
}

function labelsIn(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".price-bar-label")].map((n) => n.textContent ?? "");
}

describe("SingleLegOrderTicket quote telemetry", () => {
  it("renders the nine shared telemetry fields when priceData is threaded", () => {
    const { container } = renderTicket({ priceData: LIVE_QUOTE, quoteLabel: "AAPL" });
    const labels = labelsIn(container);
    for (const label of NINE_LABELS) {
      expect(labels).toContain(label);
    }
  });

  it("renders the instrument label above the fields", () => {
    const { container } = renderTicket({ priceData: LIVE_QUOTE, quoteLabel: "AAPL" });
    expect(labelsIn(container)[0]).toBe("AAPL");
  });

  it("uses the tight density so the block fits a narrow ticket column", () => {
    const { container } = renderTicket({ priceData: LIVE_QUOTE });
    expect(container.querySelector(".price-bar--tight")).not.toBeNull();
  });

  it("renders no telemetry block at all when priceData is omitted", () => {
    const { container } = renderTicket();
    expect(container.querySelector(".price-bar")).toBeNull();
    expect(labelsIn(container)).toHaveLength(0);
  });

  it("keeps the BID/MID/ASK quick buttons alongside the telemetry block", () => {
    const { getByRole } = renderTicket({ priceData: LIVE_QUOTE });
    expect(getByRole("button", { name: "BID" })).toBeTruthy();
    expect(getByRole("button", { name: "MID" })).toBeTruthy();
    expect(getByRole("button", { name: "ASK" })).toBeTruthy();
  });
});
