/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { TradeEntry } from "../lib/types";

const mockTrades: TradeEntry[] = [
  {
    id: 1,
    date: "2026-07-01",
    close_date: "2026-07-01",
    ticker: "MU",
    structure: "Short Put",
    decision: "CLOSED",
    realized_pnl: 1000,
    contracts: 5,
  },
  {
    id: 2,
    date: "2026-06-01",
    close_date: "2026-06-15",
    ticker: "AAPL",
    structure: "Long Call",
    decision: "CLOSED",
    realized_pnl: 5000,
    contracts: 2,
  },
  {
    id: 3,
    date: "2026-07-05",
    ticker: "MSFT",
    structure: "Long Call",
    decision: "OPEN",
    action: "BUY_OPTION",
    contracts: 1,
  },
];

vi.mock("@/lib/useJournal", () => ({
  useJournal: () => ({
    data: { trades: mockTrades },
    loading: false,
    error: null,
    syncWithIB: vi.fn(),
    syncing: false,
    lastSyncResult: null,
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true, width: 1200 }),
}));

vi.mock("@/lib/useTickerNav", () => ({
  useTickerNav: () => ({ navigateToTicker: vi.fn() }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/journal",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/journal/rangePnl", async () => {
  const actual = await vi.importActual<typeof import("../lib/journal/rangePnl")>(
    "../lib/journal/rangePnl",
  );
  return {
    ...actual,
    rangeForPreset: (preset: Parameters<typeof actual.rangeForPreset>[0], now?: Date) =>
      actual.rangeForPreset(preset, now ?? new Date("2026-07-09T18:00:00.000Z")),
  };
});

vi.mock("../components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));

vi.mock("@/components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));

import WorkspaceSections from "../components/WorkspaceSections";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-07-09T18:00:00.000Z"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Journal range P&L UI", () => {
  it("defaults to MTD and shows realized P&L for July closes only", () => {
    render(React.createElement(WorkspaceSections, { section: "journal" }));

    expect(screen.getByTestId("journal-range-bar")).toBeTruthy();
    // MTD: only MU +1000 (AAPL closed in June excluded)
    expect(screen.getByTestId("journal-range-realized").textContent).toMatch(/\+\$1,000|\+\$1000/);
    expect(screen.getByTestId("journal-range-closed").textContent).toBe("1");
    // Activity in MTD includes July open MSFT
    expect(screen.getByTestId("journal-range-open").textContent).toBe("1");
    expect(screen.getByText("MU")).toBeTruthy();
    expect(screen.queryByText("AAPL")).toBeNull();
    expect(screen.getByText("MSFT")).toBeTruthy();
  });

  it("All preset includes prior-month closes", () => {
    render(React.createElement(WorkspaceSections, { section: "journal" }));
    fireEvent.click(screen.getByTestId("journal-range-all"));
    expect(screen.getByTestId("journal-range-realized").textContent).toMatch(/\+\$6,000|\+\$6000/);
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByTestId("journal-range-label").textContent).toMatch(/All/i);
  });

  it("custom range with no activity shows empty state and recovers on All", () => {
    render(React.createElement(WorkspaceSections, { section: "journal" }));
    fireEvent.click(screen.getByTestId("journal-range-custom"));
    fireEvent.change(screen.getByTestId("journal-range-from"), {
      target: { value: "2025-01-01" },
    });
    fireEvent.change(screen.getByTestId("journal-range-to"), {
      target: { value: "2025-01-31" },
    });
    expect(screen.getByTestId("journal-range-empty")).toBeTruthy();
    expect(screen.getByTestId("journal-trade-count").textContent).toMatch(/0\s+TRADES/);
    fireEvent.click(screen.getByRole("button", { name: /show all/i }));
    expect(screen.queryByTestId("journal-range-empty")).toBeNull();
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByTestId("journal-range-realized").textContent).toMatch(/\+\$6,000|\+\$6000/);
  });
});
