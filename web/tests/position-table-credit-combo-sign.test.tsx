/**
 * @vitest-environment jsdom
 *
 * A net-CREDIT combo's Avg Entry and Initial Value must carry the credit sign
 * (negative) — bug 2026-06-23: EWY "Ratio Reverse Risk Reversal 5x30" showed
 * +$66.87 / +$33,435 when the position was opened for a $33,436 CREDIT. The
 * sign was correct at resolveEntryCost but stripped by Math.abs in getAvgEntry
 * and the initial_value cell. P&L is unaffected (it abs's only its own %
 * denominator). A DEBIT combo stays positive (the existing initial-value suite
 * covers that), proving the fix tracks the net sign, not a blanket negate.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import PositionTable from "../components/PositionTable";
import { getAvgEntry } from "../lib/positionUtils";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);

// EWY ratio reverse risk reversal 5x30 — SHORT 30x Call $215 + LONG 5x Put $180,
// opened for a net credit. resolveEntryCost = +6,673 (long) − 40,109 (short) =
// −33,436; market value = +5,450 − 31,050 = −25,600; P&L = −25,600 − (−33,436)
// = +7,836.
const EWY_CREDIT_RR: PortfolioPosition = {
  id: 1,
  ticker: "EWY",
  structure: "Ratio Reverse Risk Reversal 5x30 (P$180.0/C$215.0)",
  structure_type: "Risk Reversal",
  risk_profile: "undefined",
  expiry: "2026-07-17",
  contracts: 5,
  direction: "COMBO",
  entry_cost: -33436,
  max_risk: null,
  market_value: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-06-01",
  legs: [
    { direction: "SHORT", contracts: 30, type: "Call", strike: 215, entry_cost: 40109, avg_cost: -13.37, market_price: 10.35, market_value: 31050 },
    { direction: "LONG", contracts: 5, type: "Put", strike: 180, entry_cost: 6673, avg_cost: 13.35, market_price: 10.9, market_value: 5450 },
  ],
};

function cellByHeader(tr: HTMLElement, header: string): string {
  const headers = Array.from(document.querySelectorAll("thead th")).map((th) => th.textContent?.trim() ?? "");
  const idx = headers.findIndex((h) => h === header);
  const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim() ?? "");
  return cells[idx] ?? "";
}

describe("PositionTable — net-credit combo signs (EWY ratio reverse RR)", () => {
  it("getAvgEntry is negative for a net-credit combo", () => {
    // −33,436 / (5 × 100) = −66.872
    expect(getAvgEntry(EWY_CREDIT_RR)).toBeCloseTo(-66.872, 2);
  });

  it("renders negative Avg Entry + Initial Value, P&L still positive", () => {
    render(<PositionTable positions={[EWY_CREDIT_RR]} prices={{}} />);
    const tr = screen.getByText("EWY").closest("tr")!;

    const avg = cellByHeader(tr, "Avg Entry");
    expect(avg).toContain("-");
    expect(avg).toContain("66.87");

    const iv = cellByHeader(tr, "Initial Value");
    expect(iv).toContain("-");
    expect(iv).toContain("33,436");

    // P&L must stay POSITIVE (+$7,836) — signing avg/initial doesn't touch it.
    const pnl = cellByHeader(tr, "P&L");
    expect(pnl).not.toMatch(/^-/);
    expect(pnl).toContain("7,83");
  });
});
