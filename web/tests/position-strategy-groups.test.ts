import { describe, expect, it } from "vitest";
import type { PortfolioPosition } from "../lib/types";
import {
  defaultStrategyGroups, describeStrategyLegs, projectStrategyGroups,
  strategySourceIdentity, suggestStrategyGroups, validateStrategyGroups,
} from "../lib/positionStrategyGroups";
import { describeDisplayReturnBasis, getDisplayReturnPct, getPnlDollars, resolveDisplayReturnBasis, resolveReturnCapital } from "../lib/positionUtils";

function aaoi(): PortfolioPosition {
  return {
    id: 42, account_id: "DU123", position_instance_id: "aaoi-opening-1", ticker: "AAOI",
    structure: "Combo (3 legs)", structure_type: "Combo", risk_profile: "undefined",
    expiry: "2026-11-20", contracts: 60, direction: "COMBO", entry_cost: -2386,
    market_value: -18200, max_risk: 99999, ib_daily_pnl: 810, init_margin_at_entry: 24000,
    kelly_optimal: 0.1, target: 123, stop: 90, entry_date: "2026-10-13", basis_source: "ib",
    legs: [
      { con_id: 101, type: "Call", strike: 105, direction: "LONG", contracts: 60, entry_cost: 59340, avg_cost: 989, market_price: 7.2, market_value: 43200, basis_source: "ib" },
      { con_id: 102, type: "Call", strike: 140, direction: "SHORT", contracts: 60, entry_cost: 17340, avg_cost: 289, market_price: 1.9, market_value: 11400, basis_source: "ib" },
      { con_id: 103, type: "Put", strike: 90, direction: "SHORT", contracts: 50, entry_cost: 44386, avg_cost: 887.72, market_price: 10, market_value: 50000, basis_source: "ib" },
    ],
  };
}

describe("display-only strategy partition", () => {
  it("suggests the 60 bull calls separately from 50 short puts and conserves signed dollars", () => {
    const source = aaoi();
    const before = JSON.stringify(source);
    const groups = suggestStrategyGroups(source)!;
    const [spread, put] = projectStrategyGroups(source, groups)!;
    expect(groups.map(group => group.name)).toEqual(["Bull Call Spread", "Short Put"]);
    expect(spread).toMatchObject({ contracts: 60, entry_cost: 42000, market_value: 31800, max_risk: 42000, risk_profile: "defined" });
    expect(put).toMatchObject({ contracts: 50, entry_cost: -44386, market_value: -50000, max_risk: null, risk_profile: "undefined" });
    expect(spread.entry_cost! + put.entry_cost!).toBe(source.entry_cost);
    expect(spread.market_value! + put.market_value!).toBe(source.market_value);
    expect(spread.id).not.toBe(put.id);
    expect(spread.id).not.toBe(source.id);
    expect(JSON.stringify(source)).toBe(before);
    expect(resolveReturnCapital(spread)?.amount).toBe(42000);
    expect(resolveReturnCapital(put)).toBeNull();
  });

  it("never assigns parent daily P&L, measured margin, or trading targets to subsets", () => {
    const source = aaoi();
    for (const row of projectStrategyGroups(source, suggestStrategyGroups(source))!) {
      expect(row).toMatchObject({ ib_daily_pnl: null, return_capital: null, init_margin_at_entry: null,
        init_margin_at_entry_metadata: null, kelly_optimal: null, target: null, stop: null });
    }
  });

  it("keeps layout and row identity across refresh, leg reorder, and price changes", () => {
    const source = aaoi();
    const refreshed = { ...source, id: 800, market_value: 10, legs: [...source.legs].reverse().map(leg => ({ ...leg, market_value: 1 })) };
    expect(strategySourceIdentity(refreshed)).toEqual(strategySourceIdentity(source));
    const groups = suggestStrategyGroups(source)!;
    expect(validateStrategyGroups(refreshed, groups)).toEqual(groups);
    expect(projectStrategyGroups(refreshed, groups)!.map(row => row.id)).toEqual(projectStrategyGroups(source, groups)!.map(row => row.id));
  });

  it.each(["quantity", "direction", "expiry", "identity", "account", "opening"])("invalidates a saved fingerprint after %s changes", change => {
    const source = aaoi();
    const next = structuredClone(source);
    if (change === "quantity") next.legs[0].contracts++;
    if (change === "direction") next.legs[0].direction = "SHORT";
    if (change === "expiry") next.legs[0].expiry = "2026-12-18";
    if (change === "identity") next.legs[0].con_id = 1001;
    if (change === "account") next.account_id = "DU456";
    if (change === "opening") next.position_instance_id = "new-opening";
    expect(strategySourceIdentity(next)?.fingerprint).not.toBe(strategySourceIdentity(source)?.fingerprint);
  });

  it("keeps legacy accountless layouts in memory only", () => {
    const source = aaoi();
    delete source.account_id;
    expect(strategySourceIdentity(source)?.persistent).toBe(false);
  });

  it.each(["duplicate", "missing", "foreign", "empty", "name", "id"])("rejects %s assignments", mutation => {
    const source = aaoi();
    const groups = suggestStrategyGroups(source)!;
    if (mutation === "duplicate") groups[1].legIds.push(groups[0].legIds[0]);
    if (mutation === "missing") groups[1].legIds.pop();
    if (mutation === "foreign") groups[1].legIds[0] = "not-held";
    if (mutation === "empty") groups[0].legIds = [];
    if (mutation === "name") groups[0].name = "\n";
    if (mutation === "id") groups[1].id = groups[0].id;
    expect(validateStrategyGroups(source, groups)).toBeNull();
    expect(projectStrategyGroups(source, groups)).toBeNull();
  });

  it("supports manual whole-leg groups without inventing a defined risk structure", () => {
    const source = aaoi();
    const ids = describeStrategyLegs(source).map(leg => leg.id);
    const rows = projectStrategyGroups(source, [
      { id: "a", name: "Custom paired options", legIds: [ids[0], ids[2]] },
      { id: "b", name: "Residual short call", legIds: [ids[1]] },
    ])!;
    expect(rows[0]).toMatchObject({ contracts: 10, risk_profile: "complex", max_risk: null });
    expect(rows[1]).toMatchObject({ contracts: 60, risk_profile: "undefined", max_risk: null });
  });

  it("does not suggest ratio calls or calendar calls", () => {
    const source = aaoi();
    source.legs[1].contracts = 30;
    expect(suggestStrategyGroups(source)).toBeNull();
    source.legs[1].contracts = 60;
    source.legs[1].expiry = "2026-12-18";
    expect(suggestStrategyGroups(source)).toBeNull();
  });

  it("leaves basis and risk capital unavailable for mixed or unproven basis", () => {
    const source = aaoi();
    source.basis_source = "mixed";
    source.legs[1].basis_source = "session_fills";
    const spread = projectStrategyGroups(source, suggestStrategyGroups(source))![0];
    expect(spread.entry_cost).toBeNull();
    expect(spread.max_risk).toBeNull();
    expect(resolveReturnCapital(spread)).toBeNull();
    source.legs.forEach(leg => { delete leg.basis_source; });
    delete source.basis_source;
    expect(projectStrategyGroups(source, suggestStrategyGroups(source))![0].max_risk).toBeNull();
  });

  it("never publishes a partial market value", () => {
    const source = aaoi();
    source.legs[1].market_value = null;
    expect(projectStrategyGroups(source, suggestStrategyGroups(source))![0].market_value).toBeNull();
  });

  it("keeps calendar risk capital unavailable while labeling measured net-debit return", () => {
    const source = aaoi();
    source.legs[1].expiry = "2026-12-18";
    const legs = describeStrategyLegs(source);
    const rows = projectStrategyGroups(source, [
      { id: "calendar", name: "Call calendar", legIds: [legs[0].id, legs[1].id] },
      { id: "put", name: "Short put", legIds: [legs[2].id] },
    ])!;
    expect(rows[0]).toMatchObject({ contracts: 60, entry_cost: 42000, market_value: 31800,
      risk_profile: "complex", max_risk: null, expiry: "Mixed", strategy_group_capital_verified: false });
    expect(resolveReturnCapital(rows[0])).toBeNull();
    expect(resolveDisplayReturnBasis(rows[0])?.kind).toBe("net-entry-debit");
    expect(describeDisplayReturnBasis(resolveDisplayReturnBasis(rows[0]))).toBe("Return on net entry debit · $42,000");
    expect(getDisplayReturnPct(rows[0])).toBeCloseTo(-10200 / 42000 * 100);
    expect(getPnlDollars(rows[0])! + getPnlDollars(rows[1])!).toBe(getPnlDollars(source));
    expect(source).not.toHaveProperty("strategy_group_capital_verified");
  });

  it("rejects duplicate contracts, malformed quantities, and stock-containing positions", () => {
    const source = aaoi();
    source.legs[2].con_id = source.legs[0].con_id;
    expect(describeStrategyLegs(source)).toEqual([]);
    source.legs[2].con_id = 103;
    source.legs[0].contracts = 0.5;
    expect(describeStrategyLegs(source)).toEqual([]);
    source.legs[0].contracts = 60;
    source.legs[0].type = "Stock";
    expect(describeStrategyLegs(source)).toEqual([]);
  });

  it("restoring a whole layout preserves canonical broker capital and daily P&L", () => {
    const source = aaoi();
    const row = projectStrategyGroups(source, defaultStrategyGroups(source))![0];
    expect(row).toEqual(source);
    expect(row).not.toBe(source);
  });
});
