/**
 * @vitest-environment jsdom
 *
 * Buy-to-close-a-SHORT stock must surface realised P&L (bug 2026-06-23: covering
 * half a 1000-share MU short at $1,030 showed only "Total: $515,000" with no
 * P&L). Two compounding causes, both in StockOrderForm's holdings derivation:
 *   1. held shares were read only from `portfolio` (stale while IB awaits 2FA),
 *      ignoring the focused `position` prop → heldShort=0 → no close detected.
 *   2. cost basis was accumulated only for LONG legs → a short's avgCost=0.
 * `deriveStockHoldings` now prefers the focused position and bases BOTH sides.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";

import { deriveStockHoldings } from "../components/ticker-detail/BookTab";
import { useOrderRisk } from "@/lib/order/risk";
import type { PortfolioPosition, PortfolioData } from "@/lib/types";

afterEach(cleanup);

const MU_SHORT: PortfolioPosition = {
  id: 5,
  ticker: "MU",
  structure: "Stock (-1000.0 shares)",
  structure_type: "Stock",
  risk_profile: "equity",
  expiry: "N/A",
  contracts: 1000,
  direction: "SHORT",
  entry_cost: -1134970,
  max_risk: null,
  market_value: -1060600,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-06-01",
  legs: [
    { direction: "SHORT", contracts: 1000, type: "Stock", strike: 0, entry_cost: 1134970, avg_cost: 1134.97, market_price: 1060.6, market_value: 1060600 },
  ],
};

describe("deriveStockHoldings", () => {
  it("detects a SHORT holding from the focused position even when portfolio is null (stale IB)", () => {
    expect(deriveStockHoldings(MU_SHORT, null, "MU")).toEqual({ heldLong: 0, heldShort: 1000, avgCost: 1134.97 });
  });

  it("detects a LONG holding from the focused position", () => {
    const long = {
      ...MU_SHORT,
      direction: "LONG",
      legs: [{ ...MU_SHORT.legs[0], direction: "LONG" as const }],
    } as PortfolioPosition;
    expect(deriveStockHoldings(long, null, "MU")).toEqual({ heldLong: 1000, heldShort: 0, avgCost: 1134.97 });
  });

  it("falls back to portfolio aggregation with basis for BOTH directions", () => {
    const pf = { positions: [MU_SHORT] } as unknown as PortfolioData;
    const r = deriveStockHoldings(null, pf, "MU");
    expect(r.heldShort).toBe(1000);
    expect(r.avgCost).toBeCloseTo(1134.97, 2);
  });

  it("returns zeros with no position and no portfolio", () => {
    expect(deriveStockHoldings(null, null, "MU")).toEqual({ heldLong: 0, heldShort: 0, avgCost: 0 });
  });
});

describe("buy-to-close-short realised P&L (the input StockOrderForm now builds)", () => {
  it("covering 500 of a 1000-share short at $1,030 vs $1,134.97 basis → ≈ +$52,485", () => {
    const { heldShort, avgCost } = deriveStockHoldings(MU_SHORT, null, "MU");
    expect(heldShort).toBe(1000);

    const input = {
      type: "linear" as const,
      ticker: "MU",
      instrument: "stock" as const,
      action: "BUY" as const,
      quantity: 500,
      limitPrice: 1030,
      multiplier: 1,
      heldQuantity: 0,
      heldShortQuantity: heldShort,
      description: "BUY 500 MU @ $1,030",
      closeOut: { entryCostDollars: 500 * avgCost },
    };
    const { result } = renderHook(() => useOrderRisk(input, null));
    expect(result.current).not.toBeNull();
    const s = result.current!.summary;
    // 500 × 1134.97 (basis) − 500 × 1030 (cost to cover) = +52,485
    expect(s.estimatedPnl).toBeCloseTo(52485, 0);
    expect(s.estimatedPnlLabel).toMatch(/realized p&l/i);
  });
});
