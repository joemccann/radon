/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { TradeEntry } from "../lib/types";

const state = vi.hoisted(() => ({ trades: [] as TradeEntry[] }));

vi.mock("@/lib/useJournal", () => ({
  useJournal: () => ({
    data: { trades: state.trades },
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
vi.mock("@/lib/useTickerNav", () => ({ useTickerNav: () => ({ navigateToTicker: vi.fn() }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/journal",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));

import WorkspaceSections from "../components/WorkspaceSections";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-12T18:00:00.000Z"));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const imported = (id: number, structure: string): TradeEntry => ({
  id,
  date: "2026-10-12",
  ticker: "SNDK",
  structure,
  decision: "IB_AUTO_IMPORT",
  contracts: 1,
  entry_cost: 1580,
});

const headers = () => screen.getAllByRole("columnheader").map((h) => h.textContent?.trim() ?? "");

describe("Journal trader notation", () => {
  it("drops all-empty columns, states the IB source once, and writes strikes in trader notation", () => {
    state.trades = [imported(3390, "Short Call $270 2026-10-30"), imported(3389, "Closed Put $1200 2026-11-06")];
    render(React.createElement(WorkspaceSections, { section: "journal" }));

    const h = headers();
    for (const dead of ["Max Risk", "Realized P&L", "RoR", "Gates", "Edge", "Status"]) {
      expect(h.some((x) => x.startsWith(dead))).toBe(false);
    }
    expect(screen.queryByText("IB_AUTO_IMPORT")).toBeNull();
    expect(screen.getByTestId("journal-source-note").textContent).toBe(
      "Imported from IB · risk and gates not yet tagged",
    );
    expect(screen.getByText("-270C Oct 30")).toBeTruthy();
    expect(screen.getByText("close 1200P Nov 6")).toBeTruthy();
    expect(screen.getAllByText("Oct 12").length).toBe(2);
  });

  it("keeps populated columns and the Status column when decisions vary", () => {
    state.trades = [
      { ...imported(1, "Long Call"), decision: "OPEN", max_risk: 500, gates_passed: ["G1"] },
      imported(2, "Long Put"),
    ];
    render(React.createElement(WorkspaceSections, { section: "journal" }));
    const h = headers();
    for (const live of ["Status", "Max Risk", "Gates"]) {
      expect(h.some((x) => x.startsWith(live))).toBe(true);
    }
    expect(screen.queryByTestId("journal-source-note")).toBeNull();
  });
});
