/**
 * @vitest-environment jsdom
 *
 * Component test: PositionTable renders the "Implied" (Black-Scholes) column
 * derived from streaming impliedVol + ticker last.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

afterEach(cleanup);
import PositionTable from "../components/PositionTable";
import { bsPut } from "../lib/blackScholes";
import { yearsToExpiry } from "../lib/impliedValue";
import type { PortfolioPosition } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

const TODAY = new Date();

function pd(over: Partial<PriceData>): PriceData {
  return {
    symbol: "X",
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: TODAY.toISOString(),
    ...over,
  };
}

const expiry = "2099-05-01"; // far future so T > 0 regardless of when the test runs

const AMD_LONG_PUT: PortfolioPosition = {
  id: 1,
  ticker: "AMD",
  structure: "Long Put $295",
  structure_type: "Long Put",
  risk_profile: "defined",
  expiry,
  contracts: 75,
  direction: "LONG",
  entry_cost: 22500,
  max_risk: 22500,
  market_value: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-04-25",
  legs: [
    { direction: "LONG", contracts: 75, type: "Put", strike: 295, entry_cost: 22500, avg_cost: 3.0, market_price: 3.0, market_value: 22500 },
  ],
};

describe("PositionTable — Implied column", () => {
  it("renders 'Implied' header", () => {
    render(<PositionTable positions={[]} prices={{}} />);
    expect(screen.getByText("Implied")).toBeTruthy();
  });

  it("renders BS-derived implied price for an option position with streamed IV", () => {
    const sigma = 0.45;
    const spot = 280;
    const prices: Record<string, PriceData> = {
      AMD: pd({ last: spot }),
      [`AMD_${expiry.replace(/-/g, "")}_295_P`]: pd({ impliedVol: sigma }),
    };
    render(<PositionTable positions={[AMD_LONG_PUT]} prices={prices} />);

    const row = screen.getByText("AMD").closest("tr");
    expect(row).not.toBeNull();

    const T = yearsToExpiry(expiry, new Date())!;
    const expected = bsPut(spot, 295, T, 0, sigma);
    const expectedText = expected.toFixed(2);

    // Pull rendered numbers from the row; the implied cell is the 6th value column,
    // but assert by content rather than position to keep the test resilient.
    expect(row!.textContent).toContain(expectedText);
  });

  it("renders '—' when IV is missing", () => {
    const prices: Record<string, PriceData> = {
      AMD: pd({ last: 280 }),
      [`AMD_${expiry.replace(/-/g, "")}_295_P`]: pd({ impliedVol: null }),
    };
    render(<PositionTable positions={[AMD_LONG_PUT]} prices={prices} />);

    const headers = screen.getAllByText("Implied");
    expect(headers.length).toBeGreaterThan(0);

    const row = screen.getByText("AMD").closest("tr")!;
    // Implied cell text is "—". With no IV, the BS column should render that.
    const cells = Array.from(row.querySelectorAll("td"));
    const dashCells = cells.filter((c) => c.textContent === "—");
    expect(dashCells.length).toBeGreaterThan(0);
  });

  it("Stock-only position shows '—' (no BS price)", () => {
    const stockOnly: PortfolioPosition = {
      ...AMD_LONG_PUT,
      ticker: "TSLA",
      structure: "Stock",
      structure_type: "Stock",
      contracts: 100,
      legs: [
        {
          direction: "LONG",
          contracts: 100,
          type: "Stock",
          strike: null,
          entry_cost: 25000,
          avg_cost: 250,
          market_price: 260,
          market_value: 26000,
        },
      ],
    };
    const prices = { TSLA: pd({ last: 260 }) };
    render(<PositionTable positions={[stockOnly]} prices={prices} />);

    const row = screen.getByText("TSLA").closest("tr")!;
    const cells = Array.from(row.querySelectorAll("td"));
    const dashCells = cells.filter((c) => c.textContent === "—");
    expect(dashCells.length).toBeGreaterThan(0);
  });
});
