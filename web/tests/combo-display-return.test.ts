import { describe, expect, it } from "vitest";
import * as positionUtils from "@/lib/positionUtils";
import type { PortfolioPosition } from "@/lib/types";

// Namespace lookup keeps the tests-first baseline type-checkable while CI
// proves the display API is absent. Replace with direct imports in the fix.
const display = positionUtils as unknown as {
  getDisplayReturnPct: (pos: PortfolioPosition, mv?: number | null) => number | null;
  resolveDisplayReturnBasis: (pos: PortfolioPosition) => {
    amount: number;
    kind: string;
  } | null;
  describeDisplayReturnBasis: (basis: unknown) => string;
};

function sndk(): PortfolioPosition {
  return {
    id: 1, ticker: "SNDK", structure: "Combo (3 legs)", structure_type: "Combo",
    risk_profile: "undefined", expiry: "2026-10-23", contracts: 10, direction: "DEBIT",
    entry_cost: 15275, max_risk: null, market_value: 25850,
    kelly_optimal: null, target: null, stop: null, entry_date: "2026-10-01",
    legs: [
      { direction: "LONG", contracts: 10, type: "Call", strike: 1450, entry_cost: 52766, avg_cost: 5276.6, market_price: 63.15, market_value: 63150 },
      { direction: "SHORT", contracts: 10, type: "Call", strike: 1600, entry_cost: 13016, avg_cost: 1301.6, market_price: 17.55, market_value: -17550 },
      { direction: "SHORT", contracts: 5, type: "Put", strike: 1400, entry_cost: 24475, avg_cost: 4895, market_price: 39.5, market_value: -19750 },
    ],
  };
}

describe("combo display Return basis", () => {
  it("calculates the screenshot's 69.2% Return on the signed net entry debit", () => {
    const pos = sndk();
    expect(positionUtils.getPnlDollars(pos)).toBe(10575);
    expect(display.getDisplayReturnPct(pos)).toBeCloseTo(10575 / 15275 * 100, 8);
    expect(display.resolveDisplayReturnBasis(pos)).toMatchObject({ amount: 15275, kind: "net-entry-debit" });
    expect(display.describeDisplayReturnBasis(display.resolveDisplayReturnBasis(pos))).toContain("Return on net entry debit");
    expect(positionUtils.getPnlCapital(pos)).toBeNull();
    expect(positionUtils.resolveReturnCapital(pos)).toBeNull();
    expect(positionUtils.getPnlPct(pos)).toBeNull();
  });

  it("prefers verified max risk to the net entry fallback", () => {
    const pos = { ...sndk(), risk_profile: "defined", max_risk: 30000 };
    expect(display.resolveDisplayReturnBasis(pos)).toMatchObject({ amount: 30000, kind: "max-risk" });
    expect(display.getDisplayReturnPct(pos)).toBeCloseTo(35.25, 8);
  });

  it("uses every leg's signed entry value rather than a stale parent cost", () => {
    const pos = { ...sndk(), entry_cost: 999999 };
    pos.legs[1].entry_cost = -13016;
    expect(display.getDisplayReturnPct(pos)).toBeCloseTo(10575 / 15275 * 100, 8);
  });

  it("recalculates against an explicitly supplied current market value", () => {
    expect(display.getDisplayReturnPct(sndk(), 28780)).toBeCloseTo(13505 / 15275 * 100, 8);
    expect(display.getDisplayReturnPct(sndk(), 15275)).toBe(0);
    expect(display.getDisplayReturnPct(sndk(), 0)).toBe(-100);
  });

  it.each([0, -100])("withholds fallback for nonpositive signed net entry %s", netEntry => {
    const pos = sndk();
    pos.legs[0].entry_cost = 37491 + netEntry;
    expect(display.resolveDisplayReturnBasis(pos)).toBeNull();
    expect(display.getDisplayReturnPct(pos)).toBeNull();
  });

  it.each([null, undefined, NaN, Infinity, -Infinity])("withholds missing or invalid leg entry %s", entry => {
    const pos = sndk();
    pos.legs[0].entry_cost = entry as unknown as number;
    expect(display.resolveDisplayReturnBasis(pos)).toBeNull();
    expect(display.getDisplayReturnPct(pos)).toBeNull();
  });

  it("withholds a blended entry basis", () => {
    const pos = { ...sndk(), basis_source: "mixed" as const };
    expect(display.resolveDisplayReturnBasis(pos)).toBeNull();
    expect(display.getDisplayReturnPct(pos)).toBeNull();
  });

  it("does not extend the fallback to an uncovered single leg", () => {
    const pos = sndk();
    pos.legs = [pos.legs[2]];
    pos.direction = "SHORT";
    pos.entry_cost = -24475;
    expect(display.resolveDisplayReturnBasis(pos)).toBeNull();
  });

  it.each([null, NaN, Infinity, -Infinity])("withholds invalid current value %s", mv => {
    expect(display.getDisplayReturnPct(sndk(), mv)).toBeNull();
  });

  it("withholds Return when a required leg has no current or synced value", () => {
    const pos = sndk();
    pos.legs[0].market_price = null;
    pos.legs[0].market_value = null;
    expect(display.getDisplayReturnPct(pos)).toBeNull();
  });

  it("withholds overflowed basis and percentage", () => {
    const pos = sndk();
    pos.legs[0].entry_cost = Number.MAX_VALUE;
    pos.legs[1].direction = "LONG";
    pos.legs[1].entry_cost = Number.MAX_VALUE;
    expect(display.resolveDisplayReturnBasis(pos)).toBeNull();
    pos.legs = pos.legs.slice(0, 2);
    pos.legs[0].entry_cost = Number.MIN_VALUE;
    pos.legs[1].entry_cost = 0;
    expect(display.getDisplayReturnPct(pos, 1)).toBeNull();
  });
});
