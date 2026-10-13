import { describe, expect, it } from "vitest";
import { availablePositionTradeUnits } from "../lib/order/selectedLegReservations";
import type { OpenOrder, PortfolioPosition } from "../lib/types";

const position = {
  ticker: "CRWD", expiry: "2026-10-30", contracts: 100,
  legs: [
    { direction: "SHORT", type: "Call", strike: 270, contracts: 50 },
    { direction: "SHORT", type: "Put", strike: 230, contracts: 100 },
    { direction: "LONG", type: "Put", strike: 260, contracts: 100 },
  ],
} as PortfolioPosition;
const target = { kind: "legs" as const, indices: [1, 2] };
const leg = (strike = 230, action = "SELL", ratio = 1) => ({
  strike, action, ratio, right: "P", expiry: "20261030", conId: strike,
});
function option(patch: Partial<OpenOrder> = {}): OpenOrder {
  return {
    orderId: 1, permId: 100, symbol: "CRWD", action: "BUY", totalQuantity: 40,
    remaining: 40, filled: 0, status: "Submitted", orderType: "LMT", limitPrice: 1,
    auxPrice: null, avgFillPrice: null, tif: "DAY",
    contract: { secType: "OPT", symbol: "CRWD", strike: 230, right: "P", expiry: "20261030", conId: 230 },
    ...patch,
  };
}
function bag(patch: Partial<OpenOrder> = {}): OpenOrder {
  return option({ action: "SELL", contract: {
    secType: "BAG", symbol: "CRWD", conId: 0, strike: null, right: null, expiry: null,
    comboLegs: [leg(), leg(260, "BUY")],
  }, ...patch });
}
const available = (rows: OpenOrder[] = [], held = position) =>
  availablePositionTradeUnits(held, target, { open_orders: rows });

describe("selected close reservations", () => {
  it("requires a resolved order snapshot and a valid selection", () => {
    expect(availablePositionTradeUnits(position, target, null)).toBeNull();
    expect(availablePositionTradeUnits(position, target, undefined)).toBeNull();
    expect(availablePositionTradeUnits(position, target, {} as never)).toBeNull();
    expect(availablePositionTradeUnits(position, { kind: "legs", indices: [] }, { open_orders: [] })).toBe(0);
    expect(available()).toBe(100);
  });
  it("reserves buy-to-cover puts and sell-to-close longs individually", () => {
    expect(available([option()])).toBe(60);
    expect(available([option({ action: "SELL", contract: { ...option().contract, strike: 260 } })])).toBe(60);
    expect(available([option(), option({ remaining: 10 })])).toBe(50);
  });
  it("does not reserve opening directions or other contract identities", () => {
    expect(available([option({ action: "SELL" })])).toBe(100);
    for (const patch of [{ right: "C" }, { strike: 240 }, { expiry: "20261120" }, { symbol: "SPY" }]) {
      expect(available([option({ contract: { ...option().contract, ...patch } })])).toBe(100);
    }
  });
  it("uses remaining, otherwise total quantity less filled", () => {
    expect(available([option({ remaining: 12, filled: 28 })])).toBe(88);
    expect(available([option({ remaining: undefined, filled: 28 } as never)])).toBe(88);
    expect(available([option({ remaining: 0, filled: 40 })])).toBe(100);
  });
  it.each(["Filled", "Cancelled", "ApiCancelled", "Inactive"])("ignores terminal %s rows", status => {
    expect(available([option({ status })])).toBe(100);
  });
  it("reserves pending cancellations until broker confirms", () => {
    expect(available([option({ status: "PendingCancel" })])).toBe(60);
  });
  it("inverts SELL BAG envelopes while retaining structure actions", () => {
    expect(available([bag()])).toBe(60);
    expect(available([bag({ action: "BUY" })])).toBe(100);
    const reversed = bag();
    reversed.action = "BUY";
    reversed.contract.comboLegs = [leg(230, "BUY"), leg(260, "SELL")];
    expect(available([reversed])).toBe(60);
  });
  it("reserves overlapping full combos and matches ISO expiry", () => {
    const full = bag();
    full.contract.comboLegs = [
      { ...leg(270), right: "C", ratio: 1 },
      { ...leg(), ratio: 2, expiry: "2026-10-30" },
      leg(260, "BUY", 2),
    ];
    expect(available([full])).toBe(20);
  });
  it("converts per-leg availability into selected ratio BAG units", () => {
    const ratio = { ...position, legs: position.legs.map((l, i) => ({ ...l, contracts: i === 1 ? 30 : i === 2 ? 20 : 50 })) };
    expect(available([option({ remaining: 5 })], ratio)).toBe(8);
    expect(available([option({ action: "SELL", remaining: 5, contract: { ...option().contract, strike: 260 } })], ratio)).toBe(7);
  });
  it("does not count identifiable unrelated BAGs", () => {
    const unrelated = bag();
    unrelated.contract.comboLegs = [leg(200), leg(210, "BUY")];
    expect(available([unrelated])).toBe(100);
    expect(available([bag({ contract: { ...bag().contract, symbol: "SPY", comboLegs: undefined } })])).toBe(100);
  });
  it("fails closed for same-ticker unresolved BAG identities", () => {
    expect(available([bag({ contract: { ...bag().contract, comboLegs: undefined } })])).toBe(0);
    const unresolved = bag();
    unresolved.contract.comboLegs![0].expiry = null;
    expect(available([unresolved])).toBe(0);
  });
  it.each([NaN, -1, 1.5, Infinity])("fails closed for malformed matching remaining %s", remaining => {
    expect(available([option({ remaining })])).toBe(0);
  });
  it("fails closed for malformed actions, ratios, and option identities", () => {
    expect(available([option({ action: "UNKNOWN" })])).toBe(0);
    expect(available([option({ contract: { ...option().contract, expiry: null } })])).toBe(0);
    const malformed = bag();
    malformed.contract.comboLegs![0].ratio = 0;
    expect(available([malformed])).toBe(0);
  });
  it("floors exhausted inventory at zero", () => {
    expect(available([option({ remaining: 150 })])).toBe(0);
  });
  it("reserves single selected covers and closes", () => {
    expect(availablePositionTradeUnits(position, { kind: "legs", indices: [1] }, { open_orders: [option()] })).toBe(60);
    expect(availablePositionTradeUnits(position, { kind: "legs", indices: [2] }, { open_orders: [option()] })).toBe(100);
  });
  it("reserves selected calls and ignores stock orders", () => {
    expect(availablePositionTradeUnits(position, { kind: "legs", indices: [0] }, { open_orders: [option({
      remaining: 20, contract: { ...option().contract, strike: 270, right: "C" },
    })] })).toBe(30);
    expect(available([option({ contract: { ...option().contract, secType: "STK" } })])).toBe(100);
  });
  it("requires valid selected expiry and safe identity fields", () => {
    expect(available([], { ...position, expiry: "unresolved" })).toBe(0);
    for (const patch of [{ right: "UNKNOWN" }, { strike: null }, { strike: -1 }, { strike: Infinity }]) {
      expect(available([option({ contract: { ...option().contract, ...patch } })])).toBe(0);
    }
  });
  it("rejects malformed snapshot rows and BAG entries", () => {
    expect(available([null as never])).toBeNull();
    expect(available([1 as never])).toBeNull();
    for (const invalid of [null, { ...leg(), action: "UNKNOWN" }, { ...leg(), ratio: 1.5 }]) {
      const malformed = bag();
      malformed.contract.comboLegs = [invalid as never, leg(260, "BUY")];
      expect(available([malformed])).toBe(0);
    }
  });
  it("accepts lower-case broker identities and a missing filled count", () => {
    expect(available([option({ action: "buy", remaining: undefined, filled: undefined,
      contract: { ...option().contract, symbol: "crwd", secType: "opt", right: "p" },
    } as never)])).toBe(60);
  });
  it("combines held rows for an identical selected option without double reservation", () => {
    const duplicate = { ...position, legs: [...position.legs, { ...position.legs[1], contracts: 100 }] };
    expect(availablePositionTradeUnits(duplicate, { kind: "legs", indices: [1, 3] }, {
      open_orders: [option()],
    })).toBe(80);
  });
  it("fails closed when a same-ticker order loses its contract type", () => {
    expect(available([option({ contract: undefined } as never)])).toBe(0);
    expect(available([option({ contract: { ...option().contract, symbol: "", secType: "" } })])).toBe(0);
  });
  it("fails closed for missing option side and right", () => {
    expect(available([option({ action: undefined } as never)])).toBe(0);
    expect(available([option({ contract: { ...option().contract, right: null } })])).toBe(0);
  });
});
