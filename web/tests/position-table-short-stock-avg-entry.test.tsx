/**
 * @vitest-environment jsdom
 *
 * AVG ENTRY column on PositionTable for SHORT equity positions.
 *
 * A SHORT stock position carries a NEGATIVE position size (pos.contracts < 0).
 * The displayed Avg Entry must be the POSITIVE per-share price the user shorted
 * at, never a sign-flipped negative. The bug: getAvgEntry divided the (positive)
 * entry cost by the SIGNED contract count, yielding a negative per-share price.
 *
 * Regression guard alongside LONG stock and single-leg option, which were
 * already correct.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import PositionTable, {
  POSITION_COLUMN_DEFAULTS,
  type PositionColumnVisibility,
} from "../components/PositionTable";
import { getAvgEntry } from "../lib/positionUtils";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);
beforeEach(() => {
  window.localStorage.clear();
});

const expiry = "2099-05-01";

function makeVisibility(overrides: Partial<PositionColumnVisibility> = {}): PositionColumnVisibility {
  return { ...POSITION_COLUMN_DEFAULTS, ...overrides } as PositionColumnVisibility;
}

function getThTexts(): string[] {
  return Array.from(document.querySelectorAll("thead th")).map(
    (th) => th.textContent?.trim() ?? "",
  );
}

function avgEntryCellText(ticker: string): string {
  const tr = screen.getByText(ticker).closest("tr")!;
  const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim() ?? "");
  const headers = getThTexts();
  const idx = headers.findIndex((h) => h === "Avg Entry");
  expect(idx).toBeGreaterThan(-1);
  return cells[idx];
}

/* ─── fixtures ─────────────────────────────────────────── */

// SHORT 1000 MU @ $1,134.97/share → entry notional $1,134,969 (positive
// magnitude). pos.contracts is NEGATIVE for a short.
const MU_SHORT_STOCK: PortfolioPosition = {
  id: 200,
  ticker: "MU",
  structure: "Stock",
  structure_type: "Stock",
  risk_profile: "equity",
  expiry: "N/A",
  contracts: -1000,
  direction: "SHORT",
  entry_cost: 1134969,
  max_risk: null,
  market_value: null,
  kelly_optimal: null,
  target: null, stop: null,
  entry_date: "2026-01-15",
  legs: [
    { direction: "SHORT", contracts: 1000, type: "Stock", strike: null,
      entry_cost: 1134969, avg_cost: 1134.969, market_price: null, market_value: null },
  ],
};

// LONG 100 MSFT @ $468.51/share (already-correct path).
const MSFT_LONG_STOCK: PortfolioPosition = {
  id: 201,
  ticker: "MSFT",
  structure: "Stock",
  structure_type: "Stock",
  risk_profile: "equity",
  expiry: "N/A",
  contracts: 100,
  direction: "LONG",
  entry_cost: 46851,
  max_risk: 46851,
  market_value: null,
  kelly_optimal: null,
  target: null, stop: null,
  entry_date: "2026-01-15",
  legs: [
    { direction: "LONG", contracts: 100, type: "Stock", strike: null,
      entry_cost: 46851, avg_cost: 468.51, market_price: null, market_value: null },
  ],
};

// LONG 75 AMD $295 PUTs @ $3.00/share → per-contract avg entry $3.00.
const AMD_LONG_PUT: PortfolioPosition = {
  id: 202,
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

/* ─── getAvgEntry unit ─────────────────────────────────── */

describe("getAvgEntry — pure", () => {
  it("SHORT stock returns a POSITIVE per-share entry price", () => {
    expect(getAvgEntry(MU_SHORT_STOCK)).toBeCloseTo(1134.969, 3);
  });

  it("LONG stock returns the per-share entry price", () => {
    expect(getAvgEntry(MSFT_LONG_STOCK)).toBeCloseTo(468.51, 2);
  });

  it("single-leg option returns the per-contract (per-share) entry, no 100× double-count", () => {
    expect(getAvgEntry(AMD_LONG_PUT)).toBeCloseTo(3.0, 2);
  });
});

/* ─── rendered cell ────────────────────────────────────── */

describe("PositionTable — Avg Entry never negative for SHORT stock", () => {
  it("renders +$1,134.97 (positive) for a SHORT MU stock position", () => {
    render(
      <PositionTable
        positions={[MU_SHORT_STOCK]}
        prices={{}}
        columnVisibility={makeVisibility({ avg_entry: true })}
      />,
    );
    const cell = avgEntryCellText("MU");
    expect(cell).not.toMatch(/^-/);
    expect(cell).toBe("$1,134.97");
  });

  it("LONG MSFT stock still renders the correct positive avg entry", () => {
    render(
      <PositionTable
        positions={[MSFT_LONG_STOCK]}
        prices={{}}
        columnVisibility={makeVisibility({ avg_entry: true })}
      />,
    );
    expect(avgEntryCellText("MSFT")).toBe("$468.51");
  });

  it("single-leg option still renders per-contract avg entry (no regression)", () => {
    render(
      <PositionTable
        positions={[AMD_LONG_PUT]}
        prices={{}}
        columnVisibility={makeVisibility({ avg_entry: true })}
      />,
    );
    expect(avgEntryCellText("AMD")).toBe("$3.00");
  });
});
