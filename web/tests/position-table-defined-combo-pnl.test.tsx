/**
 * @vitest-environment jsdom
 *
 * A mixed-provenance combo has no trustworthy aggregate entry-cost or return
 * denominator, but its displayed per-leg P&Ls are each measured. The parent
 * P&L must equal their signed sum.
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PositionTable from "../components/PositionTable";
import { getPnlDollars, resolveEntryCost } from "../lib/positionUtils";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);

const ARM: PortfolioPosition = {
  id: 1,
  ticker: "ARM",
  structure: "Combo (3 legs)",
  structure_type: "Combo (3 legs)",
  risk_profile: "defined",
  expiry: "2026-09-18",
  contracts: 10,
  direction: "LONG",
  entry_cost: null,
  max_risk: null,
  market_value: -1_393,
  basis_source: "mixed",
  kelly_optimal: null,
  target: null,
  stop: null,
  legs: [
    {
      direction: "SHORT", contracts: 10, type: "Call", strike: 260,
      entry_cost: 8_000, avg_cost: 800, market_price: 8.6, market_value: 8_600,
      basis_source: "session_fills",
    },
    {
      direction: "LONG", contracts: 10, type: "Call", strike: 270,
      entry_cost: 10_380, avg_cost: 1_038, market_price: 5.525, market_value: 5_525,
      basis_source: "ib",
    },
    {
      direction: "LONG", contracts: 10, type: "Put", strike: 220,
      entry_cost: 7_030, avg_cost: 703, market_price: 1.682, market_value: 1_682,
      basis_source: "ib",
    },
  ],
} as PortfolioPosition;

function cellUnder(header: string): string {
  const headers = Array.from(document.querySelectorAll("thead th")).map(
    (th) => th.textContent?.trim() ?? "",
  );
  const index = headers.findIndex((value) => value.startsWith(header));
  const row = screen.getByText("ARM").closest("tr")!;
  return row.querySelectorAll("td")[index]?.textContent?.trim() ?? "";
}

describe("defined option combo aggregate P&L", () => {
  it("sums the three measured leg P&Ls while aggregate basis stays unavailable", () => {
    expect(resolveEntryCost(ARM)).toBeNull();
    expect(getPnlDollars(ARM)).toBe(-10_803);
  });

  it("renders the signed leg sum on the parent row and keeps Return unavailable", () => {
    render(<PositionTable positions={[ARM]} prices={{}} />);

    expect(cellUnder("P&L")).toBe("-$10,803");
    expect(cellUnder("Return %")).toBe("N/A");

    fireEvent.click(screen.getByRole("button", { name: "Expand legs for ARM" }));
    expect(screen.getByText("SHORT Call $260").closest("tr")?.textContent).toContain("-$600");
    expect(screen.getByText("LONG Call $270").closest("tr")?.textContent).toContain("-$4,855");
    expect(screen.getByText("LONG Put $220").closest("tr")?.textContent).toContain("-$5,348");
  });
});
