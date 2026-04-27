/**
 * @vitest-environment jsdom
 */

import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import PositionTable from "../components/PositionTable";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("../components/InstrumentDetailModal", () => ({
  default: () => null,
}));

const POSITIONS: PortfolioPosition[] = [
  {
    id: 13,
    ticker: "TSLA",
    structure: "Ratio Risk Reversal 75x10 (P$400.0/C$410.0)",
    structure_type: "Ratio Risk Reversal",
    risk_profile: "undefined",
    expiry: "2026-06-19",
    contracts: 75,
    direction: "COMBO",
    entry_cost: 118200,
    max_risk: null,
    market_value: 51975,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-04-15",
    legs: [
      {
        direction: "LONG",
        contracts: 75,
        type: "Call",
        strike: 410,
        entry_cost: 145875,
        avg_cost: 1945,
        market_price: 10.45,
        market_value: 78375,
      },
      {
        direction: "SHORT",
        contracts: 10,
        type: "Put",
        strike: 400,
        entry_cost: -27690,
        avg_cost: -2769,
        market_price: 26.41,
        market_value: -26410,
      },
    ],
  },
];

describe("PositionTable ratio risk reversal labels", () => {
  it("renders raw long-short contract counts instead of a reduced ratio", () => {
    render(<PositionTable positions={POSITIONS} prices={{}} />);

    expect(screen.getByText("Ratio Risk Reversal 75x10 (P$400.0/C$410.0)")).toBeTruthy();
    expect(screen.queryByText(/Ratio Risk Reversal 2x15/)).toBeNull();
  });
});
