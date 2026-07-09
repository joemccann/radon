/**
 * @vitest-environment jsdom
 *
 * "Initial Value" column on PositionTable.
 *
 * Initial Value = the notional put on at entry.
 *   stock         : qty × avg_entry              (multiplier 1, positive)
 *   single option : contracts × avg_entry × 100, SIGNED — a SHORT option is a
 *                   premium CREDIT and reads NEGATIVE; a LONG option is a debit
 *                   paid and reads positive
 *   combo         : net entry cost, SIGNED — a net credit shows NEGATIVE
 *                   (credits negative, debits positive)
 *
 * Default ON. Toggleable in the Columns popover. Initial Value follows the same
 * sign scoping as Avg Entry (getInitialValue): a single-leg short option is a
 * CREDIT (negative), a single-leg stock and long option stay positive notionals,
 * and a multi-leg COMBO carries the net credit/debit sign (a credit was
 * received, so it reads negative). Per-leg LegRow stays |leg.entry_cost|
 * (positive).
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

// Risk reversal opened for net credit. Entry Cost is negative, and (combo)
// Initial Value carries that credit sign → negative.
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

// 20 AAOI $110 SHORT PUTs opened for an $11,574 premium credit. A single-leg
// short option's Initial Value is a CREDIT → NEGATIVE (-$11,574), mirroring the
// signed Avg Entry ($-5.79). entry_cost is stored as a positive magnitude.
const AAOI_SHORT_PUT: PortfolioPosition = {
  id: 200,
  ticker: "AAOI",
  structure: "Short Put $110.0",
  structure_type: "Short Put",
  risk_profile: "undefined",
  expiry,
  contracts: 20,
  direction: "SHORT",
  entry_cost: 11574,
  max_risk: null,
  market_value: 8140,
  kelly_optimal: null,
  target: null, stop: null,
  entry_date: "2026-04-27",
  legs: [
    { direction: "SHORT", contracts: 20, type: "Put", strike: 110,
      entry_cost: 11574, avg_cost: -5.79, market_price: 4.07, market_value: 8140 },
  ],
};

// 100 shares TSLA SHORT @ $113.49/share → notional $11,349. A single-leg SHORT
// STOCK's Initial Value stays a positive per-instrument notional (daca786).
const TSLA_SHORT_STOCK: PortfolioPosition = {
  id: 201,
  ticker: "TSLA",
  structure: "Stock",
  structure_type: "Stock",
  risk_profile: "equity",
  expiry: "N/A",
  contracts: 100,
  direction: "SHORT",
  entry_cost: 11349,
  max_risk: null,
  market_value: null,
  kelly_optimal: null,
  target: null, stop: null,
  entry_date: "2026-04-27",
  legs: [
    { direction: "SHORT", contracts: 100, type: "Stock", strike: null,
      entry_cost: 11349, avg_cost: 113.49, market_price: null, market_value: null },
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

  it("market_value appears before initial_value (swap with Initial Value)", () => {
    const keys = POSITION_COLUMNS.map((c) => c.key);
    const mvIdx = keys.indexOf("market_value");
    const ivIdx = keys.indexOf("initial_value");
    expect(mvIdx).toBeGreaterThan(-1);
    expect(ivIdx).toBeGreaterThan(-1);
    expect(mvIdx).toBeLessThan(ivIdx);
  });
});

describe("PositionTable — rendered header order swaps Market Value before Initial Value", () => {
  it("Market Value <th> precedes Initial Value <th> in the rendered table", () => {
    render(<PositionTable positions={[AAPL_STOCK]} prices={{}} />);
    const ths = getThTexts();
    const mvIdx = ths.findIndex((t) => t === "Market Value");
    const ivIdx = ths.findIndex((t) => t === "Initial Value");
    expect(mvIdx).toBeGreaterThan(-1);
    expect(ivIdx).toBeGreaterThan(-1);
    expect(mvIdx).toBeLessThan(ivIdx);
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

  it("renders a NEGATIVE Initial Value for a net-credit combo (credits are negative)", () => {
    // Risk Reversal opened for a $2,500 credit. A combo's Initial Value carries
    // the net credit/debit sign — a credit was RECEIVED, so it must show
    // -$2,500, not +$2,500 (the EWY ratio-reverse-RR bug, 2026-06-23).
    render(<PositionTable positions={[SHORT_RISK_REVERSAL]} prices={{}} />);
    const tr = screen.getByText("AAOI").closest("tr")!;
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim() ?? "");
    const headers = getThTexts();
    const ivIdx = headers.findIndex((h) => h === "Initial Value");
    expect(ivIdx).toBeGreaterThan(-1);
    // Negative (credit). Format-agnostic about "-$" vs "$-".
    expect(cells[ivIdx]).toContain("-");
    expect(cells[ivIdx]).toContain("2,500");
    expect(cells[ivIdx]).not.toBe("$2,500");
  });

  it("renders a NEGATIVE Initial Value for a single-leg SHORT option (premium credit)", () => {
    // A short put opened for an $11,574 credit. Its Initial Value is a CREDIT →
    // -$11,574, mirroring the signed Avg Entry ($-5.79). It must NOT hard-abs to
    // +$11,574. Format-agnostic about "-$" vs "$-".
    render(<PositionTable positions={[AAOI_SHORT_PUT]} prices={{}} />);
    const tr = screen.getByText("AAOI").closest("tr")!;
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim() ?? "");
    const headers = getThTexts();
    const ivIdx = headers.findIndex((h) => h === "Initial Value");
    expect(ivIdx).toBeGreaterThan(-1);
    expect(cells[ivIdx]).toContain("-");
    expect(cells[ivIdx]).toContain("11,574");
    expect(cells[ivIdx]).not.toBe("$11,574");
  });

  it("keeps a POSITIVE Initial Value for a single-leg SHORT stock (per-instrument notional)", () => {
    // A short stock's Initial Value is a per-instrument notional and stays
    // positive regardless of direction (daca786) — never negated.
    render(<PositionTable positions={[TSLA_SHORT_STOCK]} prices={{}} />);
    const tr = screen.getByText("TSLA").closest("tr")!;
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim() ?? "");
    const headers = getThTexts();
    const ivIdx = headers.findIndex((h) => h === "Initial Value");
    expect(ivIdx).toBeGreaterThan(-1);
    expect(cells[ivIdx]).toContain("11,349");
    expect(cells[ivIdx]).not.toContain("-");
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
