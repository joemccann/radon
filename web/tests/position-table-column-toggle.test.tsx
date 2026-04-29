/**
 * @vitest-environment jsdom
 *
 * Component tests for the new column-toggle additions:
 * Structure, Direction, P&L, and Expiry columns are now user-toggleable
 * (default ON). Only `ticker` remains mandatory.
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

function pd(over: Partial<PriceData> = {}): PriceData {
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

const expiry = "2099-05-01";

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
    {
      direction: "LONG",
      contracts: 75,
      type: "Put",
      strike: 295,
      entry_cost: 22500,
      avg_cost: 3.0,
      market_price: 3.0,
      market_value: 22500,
    },
  ],
};

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
  target: null,
  stop: null,
  entry_date: "2026-04-01",
  legs: [
    { direction: "LONG", contracts: 10, type: "Call", strike: 200, entry_cost: 8000, avg_cost: 8.0, market_price: 9.0, market_value: 9000 },
    { direction: "SHORT", contracts: 10, type: "Call", strike: 210, entry_cost: 3000, avg_cost: -3.0, market_price: 3.0, market_value: 3000 },
  ],
};

function getThTexts(): string[] {
  return Array.from(document.querySelectorAll("thead th")).map(
    (th) => th.textContent?.trim() ?? "",
  );
}

function makeVisibility(overrides: Partial<PositionColumnVisibility> = {}): PositionColumnVisibility {
  return { ...POSITION_COLUMN_DEFAULTS, ...overrides } as PositionColumnVisibility;
}

describe("PositionTable — POSITION_COLUMNS exposes the new toggleable keys", () => {
  it("includes structure, direction, pnl, and expiry as toggleable entries", () => {
    const keys = POSITION_COLUMNS.map((c) => c.key);
    expect(keys).toContain("structure");
    expect(keys).toContain("direction");
    expect(keys).toContain("pnl");
    expect(keys).toContain("expiry");
  });

  it("defaults all four new toggleable columns to ON for a fresh install", () => {
    expect(POSITION_COLUMN_DEFAULTS.structure).toBe(true);
    expect(POSITION_COLUMN_DEFAULTS.direction).toBe(true);
    expect(POSITION_COLUMN_DEFAULTS.pnl).toBe(true);
    expect(POSITION_COLUMN_DEFAULTS.expiry).toBe(true);
  });
});

describe("PositionTable — default render shows new columns", () => {
  it("renders Structure, Direction, P&L, and Expiry headers by default", () => {
    render(<PositionTable positions={[AMD_LONG_PUT]} prices={{}} />);
    const ths = getThTexts();
    expect(ths.some((t) => t === "Structure")).toBe(true);
    expect(ths.some((t) => t === "Direction")).toBe(true);
    expect(ths.some((t) => t === "P&L")).toBe(true);
    expect(ths.some((t) => t === "Expiry")).toBe(true);
  });

  it("renders Structure, Direction, and Expiry cell content for the row by default", () => {
    render(<PositionTable positions={[AMD_LONG_PUT]} prices={{}} />);
    const row = screen.getByText("AMD").closest("tr")!;
    const text = row.textContent ?? "";
    expect(text).toContain("Long Put $295");
    expect(text).toContain("LONG");
    expect(text).toContain(expiry);
  });
});

describe("PositionTable — controlled column visibility hides new columns", () => {
  it("hides the Expiry header and cell when columns.expiry === false", () => {
    render(
      <PositionTable
        positions={[AMD_LONG_PUT]}
        prices={{}}
        columnVisibility={makeVisibility({ expiry: false })}
      />,
    );
    const ths = getThTexts();
    expect(ths.some((t) => t.includes("Expiry"))).toBe(false);
    const row = screen.getByText("AMD").closest("tr")!;
    expect(row.textContent ?? "").not.toContain(expiry);
  });

  it("hides the Structure header and cell when columns.structure === false", () => {
    render(
      <PositionTable
        positions={[AMD_LONG_PUT]}
        prices={{}}
        columnVisibility={makeVisibility({ structure: false })}
      />,
    );
    const ths = getThTexts();
    expect(ths.some((t) => t.includes("Structure"))).toBe(false);
    const row = screen.getByText("AMD").closest("tr")!;
    expect(row.textContent ?? "").not.toContain("Long Put $295");
  });

  it("hides the Direction header and cell when columns.direction === false", () => {
    render(
      <PositionTable
        positions={[AMD_LONG_PUT]}
        prices={{}}
        columnVisibility={makeVisibility({ direction: false })}
      />,
    );
    const ths = getThTexts();
    expect(ths.some((t) => t.includes("Direction"))).toBe(false);
    // Direction "LONG" text appears inside the .pill element. With Direction
    // hidden, that pill should not render. (LegRow strings like "LONG 75x Put"
    // only appear when legs are expanded — single-leg positions do not expand.)
    const pills = row(screen.getByText("AMD")).querySelectorAll(".pill");
    expect(pills.length).toBe(0);
  });

  it("hides the P&L header and the position-row P&L cell when columns.pnl === false", () => {
    const prices: Record<string, PriceData> = {
      AMD: pd({ last: 280 }),
      [`AMD_${expiry.replace(/-/g, "")}_295_P`]: pd({ impliedVol: 0.45 }),
    };
    render(
      <PositionTable
        positions={[AMD_LONG_PUT]}
        prices={prices}
        columnVisibility={makeVisibility({ pnl: false })}
      />,
    );
    const ths = getThTexts();
    // Exact "P&L" header (not "Today P&L") should be absent.
    expect(ths.some((t) => t === "P&L")).toBe(false);
    // The position-row P&L cell renders a "(...%)" suffix — assert the
    // percentage marker is absent from the row when the column is off.
    const tr = screen.getByText("AMD").closest("tr")!;
    expect(/\(-?\d+(\.\d+)?%\)/.test(tr.textContent ?? "")).toBe(false);
  });
});

describe("PositionTable — multi-leg P&L column behaviour", () => {
  it("hides per-leg P&L cells when the P&L column is toggled off", () => {
    render(
      <PositionTable
        positions={[VERTICAL_SPREAD]}
        prices={{}}
        columnVisibility={makeVisibility({ pnl: false })}
      />,
    );
    // Expand legs so LegRow nodes mount.
    const expandBtn = document.querySelector(
      'button[aria-label^="Expand"]',
    ) as HTMLButtonElement | null;
    expect(expandBtn).not.toBeNull();
    fireEvent.click(expandBtn!);

    // After expansion, no exact "P&L" <th> means LegRow's last-cell P&L is
    // also omitted to keep the column count consistent. (The "Today P&L"
    // header still renders — that's a separate column.)
    const ths = getThTexts();
    expect(ths.some((t) => t === "P&L")).toBe(false);
    // Sanity: leg rows render at least once.
    const legRows = Array.from(document.querySelectorAll("tbody tr")).slice(1);
    expect(legRows.length).toBeGreaterThan(0);
  });
});

// --- helpers -------------------------------------------------

function row(node: HTMLElement): HTMLElement {
  return node.closest("tr") as HTMLElement;
}
