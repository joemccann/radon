/**
 * @vitest-environment node
 *
 * REL-051 / R-112 (P2) — a combo close-out ignores other working SELLs.
 *
 * c3e7f2b8's combo close-out branch sets `closeOut`, and `whatIfKey` returns
 * `null` whenever `closeOut != null`, so the broker what-if round-trip is
 * skipped and `okToSubmit: true` is asserted on STRUCTURE MATCH alone.
 * `findHeldComboForClose` checks the quantity against HELD UNITS ONLY and
 * ignores other working SELL orders on the same BAG, so three working
 * full-size SELL combos each classify as a pure zero-margin close.
 *
 * Two smaller defects in the same branch: `netPremium` is not sign-flipped
 * for SELL (unlike the sibling opening branch), and the branch drops `quote`,
 * so net-of-cost risk disappears on a matched close.
 */
import { describe, it, expect } from "vitest";

import {
  findHeldComboForClose,
  isPureComboClose,
  overClosesHeldCombo,
} from "../lib/order/positionTrade";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

const STRUCTURE = [
  { action: "BUY" as const, right: "P", strike: 190, expiry: "20261218", ratio: 1 },
  { action: "SELL" as const, right: "P", strike: 195, expiry: "20261218", ratio: 1 },
];

function heldSpread(units = 10): PortfolioPosition {
  return {
    ticker: "AAPL",
    structure: "Bull Put Spread",
    structure_type: "Bull Put Spread",
    risk_profile: "defined",
    contracts: units,
    expiry: "20261218",
    entry_date: "2026-08-01",
    entry_cost: -1500,
    max_risk: 3500,
    legs: [
      {
        direction: "LONG", type: "Put", strike: 190, expiry: "20261218",
        contracts: units, entry_cost: 2000, avg_cost: 200,
        market_price: 1.8, market_value: 1800,
      },
      {
        direction: "SHORT", type: "Put", strike: 195, expiry: "20261218",
        contracts: units, entry_cost: -3500, avg_cost: 350,
        market_price: 3.1, market_value: -3100,
      },
    ],
  } as unknown as PortfolioPosition;
}

const portfolio = (units = 10): PortfolioData =>
  ({ positions: [heldSpread(units)] }) as unknown as PortfolioData;

/** A working SELL combo on the same BAG. */
const workingSell = (quantity: number) => ({
  orderId: 1,
  action: "SELL",
  totalQuantity: quantity,
  status: "Submitted",
  contract: { secType: "BAG", symbol: "AAPL" },
});

describe("a close-out counts working SELL orders against the held units", () => {
  it("refuses a full-size close when a full-size SELL is already working", () => {
    const held = findHeldComboForClose({
      ticker: "AAPL",
      envelopeAction: "SELL",
      quantity: 10,
      structureLegs: STRUCTURE,
      portfolio: portfolio(10),
      workingSellUnits: 10,
    });
    expect(held).toBeNull();
  });

  it("allows the remainder when a partial SELL is working", () => {
    const held = findHeldComboForClose({
      ticker: "AAPL",
      envelopeAction: "SELL",
      quantity: 4,
      structureLegs: STRUCTURE,
      portfolio: portfolio(10),
      workingSellUnits: 6,
    });
    expect(held).not.toBeNull();
  });

  it("still matches with nothing working", () => {
    const held = findHeldComboForClose({
      ticker: "AAPL",
      envelopeAction: "SELL",
      quantity: 10,
      structureLegs: STRUCTURE,
      portfolio: portfolio(10),
    });
    expect(held).not.toBeNull();
  });

  it("bounds isPureComboClose by held minus working", () => {
    expect(isPureComboClose("SELL", 10, 10, 0)).toBe(true);
    expect(isPureComboClose("SELL", 10, 10, 10)).toBe(false);
    expect(isPureComboClose("SELL", 4, 10, 6)).toBe(true);
    expect(isPureComboClose("SELL", 5, 10, 6)).toBe(false);
  });

  it("overClosesHeldCombo counts working orders too", () => {
    expect(overClosesHeldCombo("SELL", 10, 10, 0)).toBe(false);
    expect(overClosesHeldCombo("SELL", 10, 10, 1)).toBe(true);
  });
});

describe("the working-SELL tally reads the open-orders snapshot", () => {
  it("sums working SELL BAG quantity for the ticker", async () => {
    const { workingSellComboUnits } = await import("../lib/order/positionTrade");
    expect(
      workingSellComboUnits("AAPL", { open_orders: [workingSell(6), workingSell(2)] } as never),
    ).toBe(8);
  });

  it("ignores BUYs, other tickers and non-BAG orders", async () => {
    const { workingSellComboUnits } = await import("../lib/order/positionTrade");
    const orders = {
      open_orders: [
        { ...workingSell(5), action: "BUY" },
        { ...workingSell(5), contract: { secType: "BAG", symbol: "MSFT" } },
        { ...workingSell(5), contract: { secType: "OPT", symbol: "AAPL" } },
      ],
    } as never;
    expect(workingSellComboUnits("AAPL", orders)).toBe(0);
  });

  it("excludes the order being modified so a full-size modify still reads as a close", async () => {
    const { workingSellComboUnits } = await import("../lib/order/positionTrade");
    const modifying = { ...workingSell(250), orderId: 42, permId: 9001 };
    const other = { ...workingSell(3), orderId: 43, permId: 9002 };
    expect(
      workingSellComboUnits("AAPL", { open_orders: [modifying, other] } as never, { permId: 9001 }),
    ).toBe(3);
    expect(
      workingSellComboUnits("AAPL", { open_orders: [modifying, other] } as never, { orderId: 42 }),
    ).toBe(3);
  });

  it("is zero for a missing snapshot", async () => {
    const { workingSellComboUnits } = await import("../lib/order/positionTrade");
    expect(workingSellComboUnits("AAPL", null)).toBe(0);
  });
});
