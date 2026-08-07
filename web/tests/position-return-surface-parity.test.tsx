/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import PositionTable from "../components/PositionTable";
import MobilePositionList from "../components/mobile/MobilePositionList";
import PositionTab from "../components/ticker-detail/PositionTab";
import { computeUnrealizedBreakdown } from "../lib/unrealizedBreakdown";
import { getPnlDollars, getPnlPct } from "../lib/positionUtils";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

afterEach(cleanup);

const DEFINED_CALL: PortfolioPosition = {
  id: 17,
  ticker: "GLD",
  structure: "Long Call $450",
  structure_type: "Long Call",
  risk_profile: "defined",
  expiry: "2027-01-15",
  contracts: 10,
  direction: "LONG",
  entry_cost: 5_000,
  max_risk: 10_000,
  market_value: 6_000,
  legs: [{
    direction: "LONG",
    contracts: 10,
    type: "Call",
    strike: 450,
    entry_cost: 5_000,
    avg_cost: 500,
    market_price: 6,
    market_value: 6_000,
  }],
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-08-07",
};

const PORTFOLIO = {
  positions: [DEFINED_CALL],
} as unknown as PortfolioData;

describe("position return surfaces use the shared basis", () => {
  it("keeps the breakdown helper in parity with positionUtils", () => {
    const row = computeUnrealizedBreakdown(PORTFOLIO)[0];
    expect(row.pnl).toBe(getPnlDollars(DEFINED_CALL));
    expect(row.pnlPct).toBe(getPnlPct(DEFINED_CALL));
    expect(row.pnlPct).toBe(10);
  });

  it("renders the same 10% max-risk return in desktop and mobile lists", () => {
    const desktop = render(<PositionTable positions={[DEFINED_CALL]} prices={{}} />);
    expect(screen.getByText("Return %")).toBeTruthy();
    expect(screen.getByText("+10.0%").getAttribute("title")).toContain("max risk");
    desktop.unmount();

    render(<MobilePositionList positions={[DEFINED_CALL]} prices={{}} />);
    expect(screen.getByText("Return %")).toBeTruthy();
    const returns = screen.getAllByText("+10.00%");
    expect(returns.some((node) => node.parentElement?.getAttribute("title")?.includes("max risk"))).toBe(true);
  });

  it("renders ticker detail return from the same helper and provenance", () => {
    render(<PositionTab position={DEFINED_CALL} prices={{}} portfolio={PORTFOLIO} />);
    expect(screen.getByText("Unrealized P&L / Return")).toBeTruthy();
    const value = screen.getByText(/\+\$1,000 \(10\.0%\)/);
    expect(value.getAttribute("title")).toContain("max risk");
  });
});
