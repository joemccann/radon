import { describe, expect, it } from "vitest";
import {
  buildPositionTradeOrder,
  closingActionFor,
  heldPositionTradeUnits,
  resolvePositionTradeTarget,
  type TradeAction,
} from "../lib/order/positionTrade";
import type { PortfolioLeg, PortfolioPosition } from "../lib/types";

function crwd(): PortfolioPosition {
  return {
    id: 1, ticker: "CRWD", structure: "Combo (3 legs)", structure_type: "Combo",
    risk_profile: "undefined", expiry: "2026-10-30", contracts: 100,
    direction: "COMBO", entry_cost: -3162, max_risk: null,
    market_value: 37000, kelly_optimal: null, target: null, stop: null,
    entry_date: "2026-10-12", basis_source: "journal",
    legs: [
      { direction: "SHORT", type: "Call", strike: 270, contracts: 50,
        avg_cost: 1223.24, entry_cost: -61162, market_price: 8.8, market_value: -44000, basis_source: "journal" },
      { direction: "SHORT", type: "Put", strike: 230, contracts: 100,
        avg_cost: 119.69, entry_cost: -11969, market_price: 1.7, market_value: -17000, basis_source: "journal" },
      { direction: "LONG", type: "Put", strike: 260, contracts: 100,
        avg_cost: 699.69, entry_cost: 69969, market_price: 9.8, market_value: 98000, basis_source: "journal" },
    ],
  };
}

function order(position = crwd(), indices = [1, 2], quantity = 100, action: TradeAction = "SELL") {
  return buildPositionTradeOrder({ position, target: { kind: "legs", indices }, action,
    quantity, limitPrice: 8.1, tif: "DAY" });
}

describe("selected held position legs", () => {
  it("closes only CRWD puts at 100 units and 1:1, excluding the 50 short calls", () => {
    const position = crwd();
    const selected = resolvePositionTradeTarget(position, { kind: "legs", indices: [2, 1] })!;
    expect(selected.legs).toEqual([position.legs[1], position.legs[2]]);
    expect(selected.contracts).toBe(100);
    expect(selected.entry_cost).toBe(58000);
    expect(selected.market_value).toBe(81000);
    expect(selected.max_risk).toBeNull();
    expect(heldPositionTradeUnits(position, { kind: "legs", indices: [1, 2] })).toBe(100);
    const built = order(position)!;
    expect(built.isClosing).toBe(true);
    expect(built.payload).toMatchObject({ type: "combo", action: "SELL", quantity: 100 });
    expect(built.payload.legs).toEqual([
      { action: "SELL", right: "P", strike: 230, expiry: "20261030", ratio: 1 },
      { action: "BUY", right: "P", strike: 260, expiry: "20261030", ratio: 1 },
    ]);
    expect(built.riskInput.chainLegs).toEqual([
      { action: "BUY", right: "P", strike: 230, expiry: "20261030", quantity: 100 },
      { action: "SELL", right: "P", strike: 260, expiry: "20261030", quantity: 100 },
    ]);
    expect(built.riskInput.closeOut?.entryCostDollars).toBe(58000);
    expect(order(position, [1, 2], 25)?.riskInput.closeOut?.entryCostDollars).toBe(14500);
    expect(position.legs).toHaveLength(3);
  });

  it("closes a selected call spread with held structure actions", () => {
    const position = crwd();
    position.legs[1] = { ...position.legs[1], type: "Call", contracts: 50 };
    position.legs[2] = { ...position.legs[2], type: "Call", contracts: 50 };
    expect(order(position, [1, 2], 50)?.payload.legs).toEqual([
      { action: "SELL", right: "C", strike: 230, expiry: "20261030", ratio: 1 },
      { action: "BUY", right: "C", strike: 260, expiry: "20261030", ratio: 1 },
    ]);
  });

  it("retains a signed credit basis for a selected credit put spread", () => {
    const position = crwd();
    position.legs[1].entry_cost = -80000;
    const built = order(position)!;
    expect(built.payload.action).toBe("SELL");
    expect(built.riskInput.closeOut?.entryCostDollars).toBe(-10031);
  });

  it("reduces the selected counts to 3:2 instead of using the excluded leg", () => {
    const position = crwd();
    position.legs[1].contracts = 30;
    position.legs[2].contracts = 20;
    expect(heldPositionTradeUnits(position, { kind: "legs", indices: [1, 2] })).toBe(10);
    const built = order(position, [1, 2], 4)!;
    expect((built.payload.legs as Array<{ratio: number}>).map(l => l.ratio)).toEqual([3, 2]);
    expect(built.riskInput.chainLegs.map(l => l.quantity)).toEqual([12, 8]);
    expect(built.riskInput.closeOut?.entryCostDollars).toBe(23200);
    expect(order(position, [1, 2], 11)).toBeNull();
  });

  it("uses an option BUY to cover one selected short and SELL to close one long", () => {
    const position = crwd();
    expect(closingActionFor(position, { kind: "legs", indices: [0] })).toBe("BUY");
    expect(closingActionFor(position, { kind: "legs", indices: [2] })).toBe("SELL");
    expect(closingActionFor(position, { kind: "legs", indices: [1, 2] })).toBe("SELL");
    const cover = order(position, [0], 25, "BUY")!;
    expect(cover.payload).toMatchObject({ type: "option", action: "BUY", right: "C", strike: 270, quantity: 25 });
    expect(cover.riskInput.closeOut?.entryCostDollars).toBe(-30581);
    expect(order(position, [2], 100)?.riskInput.closeOut?.entryCostDollars).toBe(69969);
    expect(order(position, [0], 51, "BUY")).toBeNull();
  });

  it("preserves per-leg expiries in a calendar subset", () => {
    const position = crwd();
    position.legs[2].expiry = "2026-11-20";
    expect((order(position)!.payload.legs as Array<{ expiry: string }>).map(l => l.expiry))
      .toEqual(["20261030", "20261120"]);
  });

  it.each([[], [1, 1], [-1], [3], [0.5], [NaN]].map(indices => ({ indices })))("refuses invalid selected indices $indices", ({ indices }) => {
    expect(resolvePositionTradeTarget(crwd(), { kind: "legs", indices })).toBeNull();
    expect(order(crwd(), indices)).toBeNull();
    expect(heldPositionTradeUnits(crwd(), { kind: "legs", indices })).toBe(0);
  });

  it.each([0, -1, 2.5, NaN, Infinity])("refuses malformed selected holdings %s", contracts => {
    const position = crwd();
    position.legs[1].contracts = contracts;
    expect(order(position)).toBeNull();
  });

  it.each([0, -1, 1.5, NaN, Infinity, 101])("refuses invalid or over-close quantity %s", quantity => {
    expect(order(crwd(), [1, 2], quantity)).toBeNull();
  });

  it("cannot add exposure through the selected-leg target", () => {
    expect(order(crwd(), [1, 2], 10, "BUY")).toBeNull();
    expect(order(crwd(), [0], 10, "SELL")).toBeNull();
    expect(order(crwd(), [2], 10, "BUY")).toBeNull();
  });

  it.each([
    { type: "Stock", strike: null }, { strike: null }, { strike: NaN },
    { strike: -1 }, { direction: "INVALID" }, { type: "INVALID" },
  ])("refuses non-option or malformed contracts %j", patch => {
    const position = crwd();
    position.legs[1] = { ...position.legs[1], ...patch } as PortfolioLeg;
    expect(order(position)).toBeNull();
  });

  it("does not borrow missing leg basis from the full position", () => {
    const position = crwd();
    position.legs[1].entry_cost = NaN;
    expect(resolvePositionTradeTarget(position, { kind: "legs", indices: [1, 2] })?.entry_cost).toBeNull();
    expect(order(position)?.riskInput.closeOut?.entryCostDollars).toBeNull();
    expect(order(position, [1], 10, "BUY")?.riskInput.closeOut?.entryCostDollars).toBeNull();
  });

  it("realises selected legs whose measured basis comes from different sources", () => {
    const position = crwd();
    position.basis_source = "mixed";
    position.legs[0].basis_source = "ib";
    expect(order(position)?.riskInput.closeOut?.entryCostDollars).toBe(58000);
    position.legs[1].basis_source = "session_fills";
    expect(resolvePositionTradeTarget(position, { kind: "legs", indices: [1, 2] })?.entry_cost).toBeNull();
    expect(order(position)?.riskInput.closeOut?.entryCostDollars).toBe(58000);
    position.legs[1].basis_source = "mixed";
    expect(order(position, [1], 10, "BUY")?.riskInput.closeOut?.entryCostDollars).toBe(-1196.9);
  });

  it("VIX bull call spread: overnight long + same-session short still reports realised P&L", () => {
    const vix: PortfolioPosition = {
      id: 9, ticker: "VIX", structure: "Bull Call Spread $20.0/$23.5", structure_type: "Bull Call Spread",
      risk_profile: "defined", expiry: "2026-10-20", contracts: 1000, direction: "DEBIT",
      entry_cost: null, max_risk: null, market_value: 65000, kelly_optimal: null, target: null,
      stop: null, entry_date: "2026-09-01", basis_source: "mixed",
      legs: [
        { direction: "LONG", type: "Call", strike: 20, contracts: 1000, avg_cost: 188.99028,
          entry_cost: 188990.28, market_price: 1.3, market_value: 130000, basis_source: "journal" },
        { direction: "SHORT", type: "Call", strike: 23.5, contracts: 1000, avg_cost: 100,
          entry_cost: 100000, market_price: 0.65, market_value: 65000, basis_source: "session_fills" },
      ],
    };
    const built = buildPositionTradeOrder({ position: vix, target: { kind: "legs", indices: [0, 1] },
      action: "SELL", quantity: 1000, limitPrice: 3.5, tif: "GTC" })!;
    expect(built.isClosing).toBe(true);
    expect(built.riskInput.closeOut?.entryCostDollars).toBeCloseTo(88990.28, 2);
  });

  it("uses published leg basis for legacy rows without source labels and leaves unknown marks unavailable", () => {
    const position = crwd();
    delete position.basis_source;
    for (const leg of position.legs) delete leg.basis_source;
    position.legs[1].market_value = null;
    const selected = resolvePositionTradeTarget(position, { kind: "legs", indices: [1, 2] })!;
    expect(selected.entry_cost).toBe(58000);
    expect(selected.market_value).toBeNull();
    expect(order(position)?.riskInput.closeOut?.entryCostDollars).toBe(58000);
  });

  it("retains signed zero basis and never substitutes avg_cost or parent aggregate", () => {
    const position = crwd();
    position.legs[1].entry_cost = 0;
    position.legs[2].entry_cost = 0;
    expect(order(position)?.riskInput.closeOut?.entryCostDollars).toBe(0);
    expect(order(position, [2], 20)?.riskInput.closeOut?.entryCostDollars).toBe(0);
    position.legs[2].entry_cost = null as unknown as number;
    expect(order(position)?.riskInput.closeOut?.entryCostDollars).toBeNull();
  });

  it("retains existing whole-combo and single-leg target semantics", () => {
    const position = crwd();
    expect(resolvePositionTradeTarget(position, { kind: "combo" })).toBe(position);
    expect(heldPositionTradeUnits(position, { kind: "combo" })).toBe(50);
    expect(heldPositionTradeUnits(position, { kind: "leg", index: 1 })).toBe(100);
  });
});
