/**
 * @vitest-environment jsdom
 */

import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import PositionTable, { resolveActiveInstrument } from "../components/PositionTable";
import type { PortfolioPosition } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("../components/InstrumentDetailModal", () => ({
  default: () => null,
}));

const PRICES: Record<string, PriceData> = {
  CROX_20260417_80_C: {
    symbol: "CROX_20260417_80_C",
    last: 4.5,
    lastIsCalculated: false,
    bid: 4.4,
    ask: 4.6,
    bidSize: 1,
    askSize: 1,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 4.25,
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
  CROX_20260417_95_C: {
    symbol: "CROX_20260417_95_C",
    last: 1.25,
    lastIsCalculated: false,
    bid: 1.2,
    ask: 1.3,
    bidSize: 1,
    askSize: 1,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 1.1,
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

const POSITIONS: PortfolioPosition[] = [
  {
    id: 101,
    ticker: "CROX",
    structure: "Bull Call Spread",
    structure_type: "Bull Call Spread",
    risk_profile: "defined",
    expiry: "2026-04-17",
    contracts: 1,
    direction: "LONG",
    entry_cost: 325,
    max_risk: 325,
    market_value: 325,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-03-19",
    legs: [
      {
        direction: "LONG",
        contracts: 1,
        type: "Call",
        strike: 80,
        entry_cost: 300,
        avg_cost: 300,
        market_price: 4.5,
        market_value: 450,
      },
      {
        direction: "SHORT",
        contracts: 1,
        type: "Call",
        strike: 95,
        entry_cost: -25,
        avg_cost: -25,
        market_price: 1.25,
        market_value: -125,
      },
    ],
  },
];

describe("PositionTable expanded leg rows", () => {
  it("renders leg market values without referencing a parent-only rtLast variable", () => {
    render(React.createElement(PositionTable, { positions: POSITIONS, prices: PRICES }));

    fireEvent.click(screen.getByLabelText("Expand legs for CROX"));

    const longLegRow = screen.getByText("LONG 1x Call $80").closest("tr");
    expect(longLegRow).not.toBeNull();
    expect(longLegRow?.textContent).toContain("$450");

    const shortLegRow = screen.getByText("SHORT 1x Call $95").closest("tr");
    expect(shortLegRow).not.toBeNull();
    expect(shortLegRow?.textContent).toContain("$125");
  });

  it("portfolio refresh invalidates stale trade modal identity", () => {
    const identity = { positionId: 101, conId: null, legIndex: 0 };
    expect(resolveActiveInstrument(POSITIONS, identity)?.leg.strike).toBe(80);
    expect(resolveActiveInstrument([], identity)).toBeNull();
    const refreshed = [{ ...POSITIONS[0], legs: [POSITIONS[0].legs[1]] }];
    expect(resolveActiveInstrument(refreshed, identity)?.leg.strike).toBe(95);
  });
});
