/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import WorkspaceSections from "../components/WorkspaceSections";
import type { PortfolioData, PortfolioPosition, TradeEntry } from "@/lib/types";

vi.mock("../components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));

const useJournalMock = vi.fn();

vi.mock("@/lib/useJournal", () => ({
  useJournal: () => useJournalMock(),
}));

const basePosition: Omit<PortfolioPosition, "risk_profile" | "id" | "ticker" | "expiry" | "contracts" | "structure" | "direction" | "entry_cost" | "max_risk" | "market_value" | "legs"> = {
  structure_type: "OPTION",
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-01",
  ib_daily_pnl: 0,
  market_price_is_calculated: false,
};

beforeEach(() => {
  useJournalMock.mockReturnValue({
    data: { trades: [] as TradeEntry[] },
    loading: false,
    error: null,
    syncWithIB: vi.fn(),
    syncing: false,
    lastSyncResult: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WorkspaceSections table search placement", () => {
  it("places position table filters in each portfolio section header", () => {
    const positions: PortfolioPosition[] = [
      {
        ...basePosition,
        id: 1,
        ticker: "AAA",
        expiry: "2026-12-20",
        contracts: 1,
        structure: "Call",
        direction: "LONG",
        entry_cost: 100,
        max_risk: 100,
        market_value: 120,
        legs: [],
        risk_profile: "defined",
      },
      {
        ...basePosition,
        id: 2,
        ticker: "BBB",
        expiry: "2026-12-21",
        contracts: 1,
        structure: "Put",
        direction: "LONG",
        entry_cost: 200,
        max_risk: 200,
        market_value: 180,
        legs: [],
        risk_profile: "undefined",
      },
      {
        ...basePosition,
        id: 3,
        ticker: "CCC",
        expiry: "N/A",
        contracts: 10,
        structure: "Stock",
        direction: "LONG",
        entry_cost: 300,
        max_risk: 300,
        market_value: 320,
        legs: [],
        risk_profile: "equity",
      },
    ];

    const portfolio: PortfolioData = {
      bankroll: 1000,
      peak_value: 1000,
      last_sync: new Date("2026-03-01T12:00:00.000Z").toISOString(),
      positions,
      total_deployed_pct: 15,
      total_deployed_dollars: 600,
      remaining_capacity_pct: 85,
      position_count: positions.length,
      defined_risk_count: 1,
      undefined_risk_count: 1,
      avg_kelly_optimal: null,
    };

    render(React.createElement(WorkspaceSections, {
      section: "portfolio",
      portfolio,
    }));

    const definedHeader = screen.getByText("Defined Risk Positions").closest(".section")?.querySelector(".section-header");
    const undefinedHeader = screen.getByText("Undefined Risk Positions").closest(".section")?.querySelector(".section-header");
    const equityHeader = screen.getByText("Equity Positions").closest(".section")?.querySelector(".section-header");

    expect(definedHeader).toBeTruthy();
    expect(undefinedHeader).toBeTruthy();
    expect(equityHeader).toBeTruthy();

    expect(within(definedHeader!).getByPlaceholderText("Filter positions...")).toBeTruthy();
    expect(within(undefinedHeader!).getByPlaceholderText("Filter positions...")).toBeTruthy();
    expect(within(equityHeader!).getByPlaceholderText("Filter positions...")).toBeTruthy();

    const definedSearch = within(definedHeader!).getByRole("textbox");
    expect(definedHeader!.contains(definedSearch)).toBe(true);
  });

  it("renders journal filter inside the trade journal header", () => {
    useJournalMock.mockReturnValue({
      data: {
        trades: [
          {
            id: 1,
            date: "2026-03-20",
            ticker: "TEST",
            structure: "Bull Call Spread",
            decision: "OPEN",
          } as TradeEntry,
        ],
      },
      loading: false,
      error: null,
      syncWithIB: vi.fn(),
      syncing: false,
      lastSyncResult: null,
    });

    render(React.createElement(WorkspaceSections, {
      section: "journal",
    }));

    const journalHeader = screen.getByText("Trade Journal").closest(".section")?.querySelector(".section-header");
    expect(journalHeader).toBeTruthy();
    expect(within(journalHeader!).getByPlaceholderText("Filter trades...")).toBeTruthy();
  });
});
