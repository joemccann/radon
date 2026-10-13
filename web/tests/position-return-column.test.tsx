/** @vitest-environment jsdom */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import PositionTable, { POSITION_COLUMN_DEFAULTS } from "../components/PositionTable";
import MobilePositionList from "../components/mobile/MobilePositionList";
import PositionTab from "../components/ticker-detail/PositionTab";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));
afterEach(cleanup);
beforeEach(() => window.localStorage.clear());

const COMBO: PortfolioPosition = {
  id: 77, ticker: "SNDK", structure: "Combo (3 legs)", structure_type: "Combo",
  risk_profile: "undefined", expiry: "2099-10-23", contracts: 10, direction: "COMBO",
  entry_cost: 15_275, max_risk: null, market_value: 25_850,
  kelly_optimal: null, target: null, stop: null, entry_date: "2026-10-01",
  legs: [
    { direction: "LONG", contracts: 10, type: "Call", strike: 1450, entry_cost: 52_766, avg_cost: 5276.6, market_price: 63.15, market_value: 63_150 },
    { direction: "SHORT", contracts: 10, type: "Call", strike: 1600, entry_cost: -13_016, avg_cost: -1301.6, market_price: 17.55, market_value: -17_550 },
    { direction: "SHORT", contracts: 5, type: "Put", strike: 1400, entry_cost: -24_475, avg_cost: -4895, market_price: 39.5, market_value: -19_750 },
  ],
};

function desktop(columns = POSITION_COLUMN_DEFAULTS) {
  render(<PositionTable positions={[COMBO]} prices={{}} columnVisibility={columns} />);
  fireEvent.click(screen.getByLabelText("Expand legs for SNDK"));
}

describe("complex combo Return column", () => {
  it("places every leg percentage under Return, leaving P&L dollar-only", () => {
    desktop();
    const headers = Array.from(document.querySelectorAll("thead th"));
    const returnIndex = headers.findIndex((node) => node.textContent?.includes("Return %"));
    const expected = ["+19.7%", "-34.8%", "+19.3%"];
    screen.getAllByTestId("position-leg-pnl-pct").forEach((node, index) => {
      expect(node.textContent).toBe(expected[index]);
      const row = node.closest("tr")!;
      expect(Array.from(row.children).indexOf(node.closest("td")!)).toBe(returnIndex);
      expect(row.querySelector('[data-testid="position-leg-pnl"]')!.closest("td")!.textContent).not.toContain("%");
    });
  });

  it("keeps Return visible when the dollar P&L column is hidden", () => {
    desktop({ ...POSITION_COLUMN_DEFAULTS, pnl: false });
    expect(screen.queryAllByTestId("position-leg-pnl")).toHaveLength(0);
    expect(screen.getAllByTestId("position-leg-pnl-pct")).toHaveLength(3);
  });

  it("hides percentages when Return is hidden while keeping dollar P&L", () => {
    desktop({ ...POSITION_COLUMN_DEFAULTS, pnl_pct: false });
    expect(screen.queryAllByTestId("position-leg-pnl-pct")).toHaveLength(0);
    expect(screen.getAllByTestId("position-leg-pnl")).toHaveLength(3);
  });

  it("calculates the pictured parent return with a labeled net entry debit", () => {
    desktop();
    const value = screen.getByText("+69.2%");
    expect(value.getAttribute("title")).toContain("net entry debit");
  });

  it("separates mobile leg Return metrics from dollar P&L headings", () => {
    render(<MobilePositionList positions={[COMBO]} prices={{}} />);
    fireEvent.click(screen.getByLabelText("SNDK Combo (3 legs)"));
    screen.getAllByTestId("position-leg-pnl-pct").forEach((node) => {
      expect(node.closest(".mobile-card__leg-cell")?.textContent).toContain("Return %");
      expect(node.closest(".mobile-card__leg-head")).toBeNull();
    });
    screen.getAllByTestId("position-leg-pnl").forEach((node) => {
      expect(node.closest(".mobile-card__leg-head")?.textContent).not.toContain("%");
    });
  });

  it("calculates the mobile parent Return from the same net entry basis", () => {
    render(<MobilePositionList positions={[COMBO]} prices={{}} />);
    expect(screen.getByText("+69.23%").parentElement?.getAttribute("title")).toContain("net entry debit");
  });

  it("keeps ticker detail Return in parity with the table and mobile card", () => {
    const portfolio = { positions: [COMBO] } as unknown as PortfolioData;
    render(<PositionTab position={COMBO} prices={{}} portfolio={portfolio} />);
    expect(screen.getByText(/\+\$10,575 \(69\.2%\)/).getAttribute("title")).toContain("net entry debit");
  });
});
