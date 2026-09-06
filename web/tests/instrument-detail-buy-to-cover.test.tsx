/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import InstrumentDetailModal from "../components/InstrumentDetailModal";
import PositionTable from "../components/PositionTable";
import type { PortfolioData, PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

// T-462: the ticket now reads live-feed connectivity from
// RealtimePricesContext at the owner surface; these specs exercise
// pricing/risk with the feed up.
vi.mock("@/lib/RealtimePricesContext", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useRealtimePrices: () => ({ ...actual.useRealtimePrices(), connected: true }),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("../components/Modal", () => ({
  default: ({
    children,
    title,
  }: {
    children: React.ReactNode;
    title: string;
  }) => (
    <div role="dialog" aria-label={title} data-testid="mock-modal">
      {children}
    </div>
  ),
}));

function price(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 1,
    askSize: 1,
    volume: 1,
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
  };
}

const SHORT_CALL: PortfolioLeg = {
  direction: "SHORT",
  contracts: 5,
  type: "Call",
  strike: 1250,
  entry_cost: -6_830,
  avg_cost: -1_366,
  market_price: 0.05,
  market_price_is_calculated: false,
  market_value: -25,
};

const LONG_PUT: PortfolioLeg = {
  direction: "LONG",
  contracts: 5,
  type: "Put",
  strike: 1125,
  entry_cost: 2_265,
  avg_cost: 453,
  market_price: 4.27,
  market_price_is_calculated: false,
  market_value: 2_135,
};

const COVERED_CALL_STOCK: PortfolioLeg = {
  direction: "LONG",
  contracts: 2_500,
  type: "Stock",
  strike: 0,
  entry_cost: 424_650,
  avg_cost: 169.86,
  market_price: 170.65,
  market_value: 426_625,
};

const COVERED_CALL_SHORT_CALL: PortfolioLeg = {
  direction: "SHORT",
  contracts: 25,
  type: "Call",
  strike: 165,
  entry_cost: -12_518,
  avg_cost: -500.72,
  market_price: 9.05,
  market_value: -22_625,
};

const COVERED_CALL: PortfolioPosition = {
  id: 84,
  ticker: "EWY",
  structure: "Covered Call $165.0",
  structure_type: "Covered Call",
  risk_profile: "defined",
  direction: "COMBO",
  contracts: 25,
  expiry: "2026-08-07",
  entry_date: "2026-07-31",
  entry_cost: 412_132,
  market_value: 404_000,
  max_risk: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  legs: [COVERED_CALL_STOCK, COVERED_CALL_SHORT_CALL],
};

const POSITION: PortfolioPosition = {
  id: 42,
  ticker: "MU",
  structure: "Short Strangle $1125.0/$1250.0",
  structure_type: "Short Strangle",
  risk_profile: "undefined",
  direction: "COMBO",
  contracts: 5,
  expiry: "2026-06-26",
  entry_date: "2026-06-26",
  entry_cost: -4_565,
  market_value: -9_275,
  max_risk: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  legs: [SHORT_CALL, LONG_PUT],
} as unknown as PortfolioPosition;

const PORTFOLIO: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 2,
  defined_risk_count: 1,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  positions: [POSITION, COVERED_CALL],
  account_summary: {
    available_funds: 536_775,
    buying_power: 1_073_550,
  },
} as unknown as PortfolioData;

const PRICES: Record<string, PriceData> = {
  MU_20260626_1250_C: price("MU_20260626_1250_C", 0.01, 0.09),
  MU_20260626_1125_P: price("MU_20260626_1125_P", 4.15, 4.39),
  EWY: price("EWY", 170.6, 170.7),
};

function enterLimitAndReview(limit: string) {
  fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: limit } });
  fireEvent.click(screen.getByRole("button", { name: /place order/i }));
}

function summaryText(): string {
  return document.querySelector(".order-confirm-summary")?.textContent ?? "";
}

describe("InstrumentDetailModal buy-to-cover", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("builds a stock-only sell when closing the equity leg of a covered call", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      return {
        ok: true,
        json: async () => url.endsWith("/api/orders/whatif")
          ? ({ initMargin: 25_000 })
          : ({ status: "submitted" }),
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <InstrumentDetailModal
        leg={COVERED_CALL_STOCK}
        ticker="EWY"
        expiry="2026-08-07"
        prices={PRICES}
        portfolio={PORTFOLIO}
        onClose={() => {}}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "EWY Stock" });
    expect(dialog.textContent).not.toContain("$0 STOCK");
    expect(dialog.textContent).not.toContain("2026-08-07");
    expect((screen.getByPlaceholderText("Shares") as HTMLInputElement).value).toBe("2500");
    expect(dialog.textContent).toContain("BID 170.60");

    fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: "170" } });
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));

    const text = summaryText();
    expect(text).toContain("SELL 2500 EWY Stock @ $170.00");
    expect(text).toContain("Proceeds:");
    expect(text).toContain("$425,000");
    expect(text).toContain("Est. Realized P&L:");
    expect(text).toContain("$350");
    expect(text).toContain("25 retained short calls uncovered");
    expect(text).toContain("UNAVAILABLE");
    expect(text).not.toContain("EWY $0 P");
    expect(text).not.toContain("$42,500,000");

    fireEvent.click(screen.getByRole("button", { name: /confirm order/i }));

    const placementCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/api/orders/place"));
    expect(placementCalls).toHaveLength(1);
    expect(JSON.parse(placementCalls[0][1]!.body as string)).toEqual({
      type: "stock",
      symbol: "EWY",
      action: "SELL",
      quantity: 2_500,
      limitPrice: 170,
      tif: "DAY",
    });
  });

  it("uses negative close-out basis for a short call buy-to-cover", () => {
    render(
      <InstrumentDetailModal
        leg={SHORT_CALL}
        ticker="MU"
        expiry="2026-06-26"
        prices={PRICES}
        portfolio={PORTFOLIO}
        onClose={() => {}}
      />,
    );

    enterLimitAndReview("0.05");

    const text = summaryText();
    expect(text).not.toMatch(/portfolio not in scope/i);
    expect(text).toContain("Close Debit:");
    expect(text).toContain("$25");
    expect(text).toContain("Est. Realized P&L:");
    expect(text).toContain("$6,805");
  });

  it("threads the portfolio from PositionTable into the instrument modal", () => {
    render(
      <PositionTable
        positions={[POSITION]}
        prices={PRICES}
        portfolio={PORTFOLIO}
      />,
    );

    fireEvent.click(screen.getByLabelText("Expand legs for MU"));
    fireEvent.click(screen.getByText("SHORT Call $1250"));
    enterLimitAndReview("0.05");

    const text = summaryText();
    expect(text).not.toMatch(/Coverage indeterminate/i);
    expect(text).toContain("Close Debit:");
    expect(text).toContain("$6,805");
  });
});
