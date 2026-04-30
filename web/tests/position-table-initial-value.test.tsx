/**
 * @vitest-environment jsdom
 *
 * "Initial Value" column on PositionTable.
 *
 * Initial Value = the unsigned notional that was put on at entry.
 *   stock         : qty × avg_entry              (multiplier 1)
 *   single option : contracts × avg_entry × 100
 *   combo         : contracts × net_avg_entry × 100  (= |entry_cost|)
 *
 * Default ON. Toggleable in the Columns popover. Always non-negative —
 * Entry Cost may be negative for credit positions; Initial Value is the
 * absolute notional the user put on.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import PositionTable, {
  POSITION_COLUMNS,
  POSITION_COLUMN_DEFAULTS,
  type PositionColumnVisibility,
} from "../components/PositionTable";
import type { PortfolioPosition } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);
beforeEach(() => {
  window.localStorage.clear();
});

const TODAY = new Date();
const expiry = "2099-05-01";

function pd(over: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "X",
    last: null, lastIsCalculated: false, bid: null, ask: null,
    bidSize: null, askSize: null, volume: null, high: null, low: null,
    open: null, close: null, week52High: null, week52Low: null,
    avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null,
    timestamp: TODAY.toISOString(),
    ...over,
  };
}

function makeVisibility(overrides: Partial<PositionColumnVisibility> = {}): PositionColumnVisibility {
  return { ...POSITION_COLUMN_DEFAULTS, ...overrides } as PositionColumnVisibility;
}

function getThTexts(): string[] {
  return Array.from(document.querySelectorAll("thead th")).map(
    (th) => th.textContent?.trim() ?? "",
  );
}

/* ─── fixtures ─────────────────────────────────────────── */

// 100 AAPL @ $200.00 → Initial Value = $20,000
const AAPL_STOCK: PortfolioPosition = {
  id: 100,
  ticker: "AAPL",
  structure: "Stock",
  structure_type: "Stock",
  risk_profile: "equity",
  expiry: "N/A",
  contracts: 100,
  direction: "LONG",
  entry_cost: 20000,
  max_risk: 20000,
  market_value: null,
  kelly_optimal: null,
  target: null, stop: null,
  entry_date: "2026-01-15",
  legs: [
    { direction: "LONG", contracts: 100, type: "Stock", strike: null,
      entry_cost: 20000, avg_cost: 200.0, market_price: null, market_value: null },
  ],
};

// 75 AMD $295 LONG PUTs @ $3.00/share → 75 × 3 × 100 = $22,500
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
  target: null, stop: null,
  entry_date: "2026-04-25",
  legs: [
    { direction: "LONG", contracts: 75, type: "Put", strike: 295,
      entry_cost: 22500, avg_cost: 3.0, market_price: 3.0, market_value: 22500 },
  ],
};

// 10 NVDA Bull Call Spread $200/$210, debit $5/spread → 10 × 5 × 100 = $5,000
const VERTICAL_SPREAD: PortfolioPosition = {
  id: 2,
  ticker: "NVDA",
  structure: "Bull Call Spread $200/$210",
  structure_type: "Bull Call Spread",
  risk_profile: "defined",
  expiry,
  contracts: 10,
  direction: "LONG",
  entry_cost: 5000,
  max_risk: 5000,
  market_value: 6000,
  kelly_optimal: null,
  target: null, stop: null,
  entry_date: "2026-04-01",
  legs: [
    { direction: "LONG", contracts: 10, type: "Call", strike: 200,
      entry_cost: 8000, avg_cost: 8.0, market_price: 9.0, market_value: 9000 },
    { direction: "SHORT", contracts: 10, type: "Call", strike: 210,
      entry_cost: 3000, avg_cost: -3.0, market_price: 3.0, market_value: 3000 },
  ],
};

// Risk reversal opened for net credit. Entry Cost is negative, Initial
// Value is its absolute magnitude.
const SHORT_RISK_REVERSAL: PortfolioPosition = {
  id: 3,
  ticker: "AAOI",
  structure: "Risk Reversal (P$145.0/C$155.0)",
  structure_type: "Risk Reversal",
  risk_profile: "undefined",
  expiry,
  contracts: 50,
  direction: "COMBO",
  entry_cost: -2500, // received credit
  max_risk: null,
  market_value: null,
  kelly_optimal: null,
  target: null, stop: null,
  entry_date: "2026-04-27",
  legs: [
    { direction: "LONG", contracts: 50, type: "Call", strike: 155,
      entry_cost: 5000, avg_cost: 1.0, market_price: 1.0, market_value: 5000 },
    { direction: "SHORT", contracts: 50, type: "Put", strike: 145,
      entry_cost: 7500, avg_cost: -1.5, market_price: 1.5, market_value: 7500 },
  ],
};

/* ─── tests ────────────────────────────────────────────── */

describe("PositionTable — POSITION_COLUMNS exposes initial_value", () => {
  it("includes initial_value as a toggleable entry", () => {
    const keys = POSITION_COLUMNS.map((c) => c.key);
    expect(keys).toContain("initial_value");
  });

  it("entry has a human-readable Initial Value label", () => {
    const entry = POSITION_COLUMNS.find((c) => c.key === "initial_value");
    expect(entry?.label).toBe("Initial Value");
  });

  it("defaults to ON for a fresh install", () => {
    expect(POSITION_COLUMN_DEFAULTS.initial_value).toBe(true);
  });
});

describe("PositionTable — Initial Value renders by default", () => {
  it("renders the Initial Value <th> by default", () => {
    render(<PositionTable positions={[AAPL_STOCK]} prices={{}} />);
    const ths = getThTexts();
    expect(ths.some((t) => t === "Initial Value")).toBe(true);
  });

  it("formats stock initial value as qty × avg_entry — 100 AAPL @ $200 = $20,000", () => {
    render(<PositionTable positions={[AAPL_STOCK]} prices={{}} />);
    const tr = screen.getByText("AAPL").closest("tr")!;
    expect(tr.textContent ?? "").toContain("$20,000");
  });

  it("formats single-leg option initial value with the 100 multiplier — 75 × $3 × 100 = $22,500", () => {
    render(<PositionTable positions={[AMD_LONG_PUT]} prices={{}} />);
    const tr = screen.getByText("AMD").closest("tr")!;
    expect(tr.textContent ?? "").toContain("$22,500");
  });

  it("formats vertical spread initial value as the net debit notional — 10 × $5 × 100 = $5,000", () => {
    render(<PositionTable positions={[VERTICAL_SPREAD]} prices={{}} />);
    const tr = screen.getByText("NVDA").closest("tr")!;
    expect(tr.textContent ?? "").toContain("$5,000");
  });

  it("never renders a negative Initial Value — credit positions show |entry_cost|", () => {
    // Risk Reversal opened for $2,500 credit. Initial Value should display
    // $2,500 (positive), not -$2,500.
    render(<PositionTable positions={[SHORT_RISK_REVERSAL]} prices={{}} />);
    const tr = screen.getByText("AAOI").closest("tr")!;
    const text = tr.textContent ?? "";
    expect(text).toContain("$2,500");
    // The Initial Value column itself must not show a negative.
    // (Entry Cost — a different column — may still show -$2,500.)
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim() ?? "");
    const headers = getThTexts();
    const ivIdx = headers.findIndex((h) => h === "Initial Value");
    expect(ivIdx).toBeGreaterThan(-1);
    expect(cells[ivIdx]).not.toMatch(/^-/);
    expect(cells[ivIdx]).toBe("$2,500");
  });
});

describe("PositionTable — Initial Value column hides via toggle", () => {
  it("hides the header when columns.initial_value === false", () => {
    render(
      <PositionTable
        positions={[AAPL_STOCK]}
        prices={{}}
        columnVisibility={makeVisibility({ initial_value: false })}
      />,
    );
    const ths = getThTexts();
    expect(ths.some((t) => t === "Initial Value")).toBe(false);
  });

  it("hides the cell when columns.initial_value === false", () => {
    // Use a fixture whose Initial Value differs from any other column so we
    // can't accidentally match the same dollar string elsewhere.
    render(
      <PositionTable
        positions={[AMD_LONG_PUT]}
        prices={{}}
        columnVisibility={makeVisibility({
          initial_value: false,
          // Hide entry_cost too so $22,500 can't sneak in via that column
          // (in this fixture they happen to be equal).
          entry_cost: false,
          market_value: false,
        })}
      />,
    );
    const tr = screen.getByText("AMD").closest("tr")!;
    expect(tr.textContent ?? "").not.toContain("$22,500");
  });
});

describe("PositionTable — Initial Value flows through to LegRow", () => {
  it("renders per-leg Initial Value (= |leg.entry_cost|) when legs are expanded", () => {
    render(
      <PositionTable
        positions={[VERTICAL_SPREAD]}
        prices={{}}
        columnVisibility={makeVisibility({
          // Hide every other dollar column so the only $-amounts left in the
          // leg row are Initial Value cells.
          avg_entry: false, last_price: false, implied: false,
          implied_market_value: false, daily_chg: false, today_pnl: false,
          entry_cost: false, market_value: false, pnl: false,
        })}
      />,
    );
    const expandBtn = document.querySelector('button[aria-label^="Expand"]') as HTMLButtonElement | null;
    expect(expandBtn).not.toBeNull();
    fireEvent.click(expandBtn!);

    const allRows = Array.from(document.querySelectorAll("tbody tr"));
    // First row = position row, subsequent = leg rows.
    const legRows = allRows.slice(1);
    expect(legRows.length).toBe(2);
    // Long leg: |8000| → "$8,000"; short leg: |3000| → "$3,000".
    expect(legRows[0].textContent ?? "").toContain("$8,000");
    expect(legRows[1].textContent ?? "").toContain("$3,000");
  });
});
