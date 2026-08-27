/**
 * @vitest-environment jsdom
 *
 * Expanded leg rows carry their contract count in the Qty column, not inline in
 * the leg description. "LONG 100x Call $300" duplicated the combo's Qty cell.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import PositionTable from "../components/PositionTable";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);
beforeEach(() => {
  window.localStorage.clear();
});

const BULL_CALL: PortfolioPosition = {
  id: 41,
  ticker: "ADBE",
  structure: "Bull Call Spread $300.0/$350.0",
  structure_type: "Bull Call Spread",
  risk_profile: "defined",
  expiry: "2099-05-01",
  contracts: 100,
  direction: "COMBO",
  entry_cost: 120000,
  max_risk: 120000,
  market_value: 140000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-04-15",
  legs: [
    {
      direction: "LONG",
      contracts: 100,
      type: "Call",
      strike: 300,
      entry_cost: 250000,
      avg_cost: 2500,
      market_price: 28.0,
      market_value: 280000,
    },
    {
      direction: "SHORT",
      contracts: 100,
      type: "Call",
      strike: 350,
      entry_cost: -130000,
      avg_cost: -1300,
      market_price: 14.0,
      market_value: -140000,
    },
  ],
};

function expandLegs() {
  fireEvent.click(screen.getByLabelText("Expand legs for ADBE"));
}

describe("PositionTable expanded leg rows — quantity placement", () => {
  it("drops the inline contract count from the leg description", () => {
    render(<PositionTable positions={[BULL_CALL]} prices={{}} />);
    expandLegs();

    expect(screen.getByText("LONG Call $300")).toBeTruthy();
    expect(screen.getByText("SHORT Call $350")).toBeTruthy();
    expect(screen.queryByText(/100x/)).toBeNull();
  });

  it("renders each leg's contract count in the Qty column", () => {
    render(<PositionTable positions={[BULL_CALL]} prices={{}} />);
    expandLegs();

    const header = screen.getByText("Qty").closest("th")!;
    const headerCells = Array.from(header.parentElement!.children);
    const qtyIndex = headerCells.indexOf(header);

    const legRow = screen.getByText("LONG Call $300").closest("tr")!;
    const cells = Array.from(legRow.children);
    expect(cells[qtyIndex]!.textContent).toBe("100");
  });
});
