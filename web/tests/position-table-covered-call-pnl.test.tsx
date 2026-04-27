/**
 * @vitest-environment jsdom
 */

import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import PositionTable from "../components/PositionTable";
import type { PortfolioPosition } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("../components/InstrumentDetailModal", () => ({
  default: () => null,
}));

const POSITIONS: PortfolioPosition[] = [
  {
    id: 1,
    ticker: "USAX",
    structure: "Covered Call $45.0 (1000 shares)",
    structure_type: "Covered Call",
    risk_profile: "defined",
    expiry: "2026-06-18",
    contracts: 1000,
    direction: "LONG",
    entry_cost: 42426.6,
    max_risk: 42426.6,
    market_value: 28670,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-03-01",
    legs: [
      {
        direction: "LONG",
        contracts: 1000,
        type: "Stock",
        strike: null,
        entry_cost: 46680,
        avg_cost: 46.68,
        market_price: 33.8,
        market_value: 33800,
      },
      {
        direction: "SHORT",
        contracts: 10,
        type: "Call",
        strike: 45,
        entry_cost: 4253.4,
        avg_cost: -425.34,
        market_price: 5.13,
        market_value: 5130,
      },
    ],
  },
];

const PRICES: Record<string, PriceData> = {
  USAX: {
    symbol: "USAX",
    last: 33.8,
    lastIsCalculated: false,
    bid: 33.75,
    ask: 33.85,
    bidSize: 1,
    askSize: 1,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: 33.38,
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
  },
  USAX_20260618_45_C: {
    symbol: "USAX_20260618_45_C",
    last: 5.13,
    lastIsCalculated: false,
    bid: 5.1,
    ask: 5.16,
    bidSize: 1,
    askSize: 1,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 4.85,
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
  },
};

describe("PositionTable covered call P&L", () => {
  it("uses stock share math instead of option contract math for covered calls", () => {
    render(<PositionTable positions={POSITIONS} prices={PRICES} showUnderlying />);

    const row = screen.getByText("USAX").closest("tr");
    expect(row).not.toBeNull();
    const text = row?.textContent ?? "";

    expect(text).toContain("$42.43");
    expect(text).toContain("$28.67");
    expect(text).toContain("$42,427");
    expect(text).toContain("$28,670");
    expect(text).toContain("-$13,757 (-32.4%)");
    expect(text).not.toContain("+$3,332,443");
  });
});
