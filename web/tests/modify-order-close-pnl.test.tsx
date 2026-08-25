/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { OpenOrder, PortfolioData, PortfolioPosition } from "@/lib/types";
import ModifyOrderModal from "@/components/ModifyOrderModal";

vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0,
}));

vi.mock("@/components/Modal", () => ({
  default: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? React.createElement("div", { className: "mock-modal" }, children) : null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function optionOrder(action: "BUY" | "SELL", quantity: number, limitPrice: number): OpenOrder {
  return {
    orderId: 95,
    permId: action === "SELL" ? 653624857 : 653624858,
    symbol: "SNDK C1570",
    contract: {
      conId: 987654,
      symbol: "SNDK",
      secType: "OPT",
      strike: 1570,
      right: "C",
      expiry: "2026-07-17",
    },
    action,
    orderType: "LMT",
    totalQuantity: quantity,
    limitPrice,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: quantity,
    avgFillPrice: null,
    tif: "DAY",
  };
}

function portfolioWithLeg(direction: "LONG" | "SHORT", contracts: number, avgCost: number): PortfolioData {
  const position: PortfolioPosition = {
    id: 1,
    ticker: "SNDK",
    structure: direction === "LONG" ? "Long Call" : "Short Call",
    structure_type: "Single",
    risk_profile: "Defined",
    expiry: "2026-07-17",
    contracts,
    direction,
    entry_cost: contracts * avgCost,
    max_risk: null,
    market_value: null,
    legs: [{
      direction,
      contracts,
      type: "Call",
      strike: 1570,
      entry_cost: contracts * avgCost,
      avg_cost: avgCost,
      market_price: null,
      market_value: null,
    }],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-07-01",
  };

  return {
    bankroll: 400_000,
    peak_value: 400_000,
    last_sync: "2026-07-15T17:42:03.000Z",
    positions: [position],
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: 1,
    defined_risk_count: 1,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    account_summary: {
      net_liquidation: 400_000,
      daily_pnl: 0,
      unrealized_pnl: 0,
      realized_pnl: 0,
      settled_cash: 400_000,
      maintenance_margin: 0,
      excess_liquidity: 400_000,
      buying_power: 800_000,
      available_funds: 400_000,
      dividends: 0,
    },
  };
}

function renderModifiedOrder(order: OpenOrder, portfolio: PortfolioData, price: string) {
  render(
    <ModifyOrderModal
      order={order}
      loading={false}
      portfolio={portfolio}
      onConfirm={vi.fn()}
      onClose={() => {}}
    />,
  );
  fireEvent.change(screen.getByLabelText(/New Limit Price/i), { target: { value: price } });
  return within(document.querySelector(".order-confirm-summary") as HTMLElement);
}

describe("ModifyOrderModal close-out P&L", () => {
  it("modify is disabled until canonical risk permits", () => {
    render(
      <ModifyOrderModal
        order={optionOrder("SELL", 4, 100)}
        loading={false}
        portfolio={undefined}
        onConfirm={vi.fn()}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/New Limit Price/i), { target: { value: "95" } });
    expect((screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("rejects fractional and exponent quantities instead of truncating them", () => {
    const onConfirm = vi.fn();
    render(
      <ModifyOrderModal
        order={optionOrder("SELL", 4, 100)}
        loading={false}
        portfolio={portfolioWithLeg("LONG", 4, 9_366.25)}
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );

    const quantity = screen.getByLabelText(/New Quantity/i);
    const modify = screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;
    for (const invalid of ["1.9", "1e2"]) {
      fireEvent.change(quantity, { target: { value: invalid } });
      expect(modify.disabled).toBe(true);
      fireEvent.click(modify);
    }
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("shows estimated realized P&L when modifying a sell-to-close long option", () => {
    const summary = renderModifiedOrder(
      optionOrder("SELL", 4, 100),
      portfolioWithLeg("LONG", 4, 9_366.25),
      "95",
    );

    expect(summary.getByText("Proceeds:")).toBeTruthy();
    expect(summary.getByText("$38,000")).toBeTruthy();
    expect(summary.getByText("Est. Realized P&L:")).toBeTruthy();
    expect(summary.getByText("$535")).toBeTruthy();
    expect(summary.queryByText("Max Gain:")).toBeNull();
    expect(summary.queryByText("Max Loss:")).toBeNull();
  });

  it("shows estimated realized P&L when modifying a buy-to-close short option", () => {
    const summary = renderModifiedOrder(
      optionOrder("BUY", 2, 4.5),
      portfolioWithLeg("SHORT", 2, 500),
      "4",
    );

    expect(summary.getByText("Close Debit:")).toBeTruthy();
    expect(summary.getByText("$800")).toBeTruthy();
    expect(summary.getByText("Est. Realized P&L:")).toBeTruthy();
    expect(summary.getByText("$200")).toBeTruthy();
  });

  it("does not classify quantity beyond the held contracts as a pure close", () => {
    const summary = renderModifiedOrder(
      optionOrder("SELL", 5, 100),
      portfolioWithLeg("LONG", 4, 9_366.25),
      "95",
    );

    expect(summary.queryByText("Est. Realized P&L:")).toBeNull();
    expect(summary.getByText("Max Loss:")).toBeTruthy();
    expect(summary.getByText("UNBOUNDED")).toBeTruthy();
  });
});

const CBRS_EXPIRY = "2026-08-21";

function cbrsComboOrder(): OpenOrder {
  return {
    orderId: 88,
    permId: 1857171999,
    symbol: "CBRS Spread",
    contract: {
      conId: 28812380,
      symbol: "CBRS",
      secType: "BAG",
      strike: 0,
      right: "?",
      expiry: null,
      comboLegs: [
        { conId: 1, ratio: 1, action: "SELL", symbol: "CBRS", strike: 200, right: "P", expiry: CBRS_EXPIRY },
        { conId: 2, ratio: 1, action: "BUY", symbol: "CBRS", strike: 205, right: "C", expiry: CBRS_EXPIRY },
      ],
    },
    action: "SELL",
    orderType: "LMT",
    totalQuantity: 50,
    limitPrice: 5,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 50,
    avgFillPrice: null,
    tif: "DAY",
  };
}

function cbrsHeldReversal(): PortfolioData {
  const position: PortfolioPosition = {
    id: 12,
    ticker: "CBRS",
    structure: "Risk Reversal (P$200.0/C$205.0)",
    structure_type: "Risk Reversal",
    risk_profile: "Undefined",
    expiry: CBRS_EXPIRY,
    contracts: 50,
    direction: "COMBO",
    entry_cost: 25_000,
    max_risk: null,
    market_value: null,
    legs: [
      {
        direction: "SHORT",
        contracts: 50,
        type: "Put",
        strike: 200,
        expiry: CBRS_EXPIRY,
        entry_cost: -30_000,
        avg_cost: 600,
        market_price: null,
        market_value: null,
      },
      {
        direction: "LONG",
        contracts: 50,
        type: "Call",
        strike: 205,
        expiry: CBRS_EXPIRY,
        entry_cost: 55_000,
        avg_cost: 1_100,
        market_price: null,
        market_value: null,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-08-21",
  };

  return {
    bankroll: 400_000,
    peak_value: 400_000,
    last_sync: "2026-08-21T14:10:53.000Z",
    positions: [position],
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: 1,
    defined_risk_count: 0,
    undefined_risk_count: 1,
    avg_kelly_optimal: null,
    account_summary: {
      net_liquidation: 400_000,
      daily_pnl: 0,
      unrealized_pnl: 0,
      realized_pnl: 0,
      settled_cash: 400_000,
      maintenance_margin: 0,
      excess_liquidity: 400_000,
      buying_power: 800_000,
      available_funds: 107_925,
      dividends: 0,
    },
  };
}

function cbrsComboPrices(): Record<string, import("@/lib/pricesProtocol").PriceData> {
  const ts = "2026-08-21T14:10:53.000Z";
  const blank = {
    lastIsCalculated: false,
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
    undPrice: 203.47,
    timestamp: ts,
  };
  return {
    CBRS: {
      ...blank,
      symbol: "CBRS",
      last: 203.47,
      bid: 203.4,
      ask: 203.55,
    },
    CBRS_20260821_200_P: {
      ...blank,
      symbol: "CBRS_20260821_200_P",
      last: 6.25,
      bid: 6.0,
      ask: 6.5,
      impliedVol: 0.55,
    },
    CBRS_20260821_205_C: {
      ...blank,
      symbol: "CBRS_20260821_205_C",
      last: 7.3,
      bid: 6.75,
      ask: 7.8,
      impliedVol: 0.5,
    },
  };
}

function renderCbrsModify(price: string, portfolio: PortfolioData) {
  render(
    <ModifyOrderModal
      order={cbrsComboOrder()}
      loading={false}
      prices={cbrsComboPrices()}
      portfolio={portfolio}
      onConfirm={vi.fn()}
      onClose={() => {}}
    />,
  );
  fireEvent.change(screen.getByLabelText(/New Net Price/i), { target: { value: price } });
  return within(document.querySelector(".order-confirm-summary") as HTMLElement);
}

describe("ModifyOrderModal combo close-out P&L", () => {
  it("shows realized P&L when modifying a SELL combo that closes a held risk reversal", () => {
    const summary = renderCbrsModify("8", cbrsHeldReversal());

    expect(summary.getByText("Close Credit:")).toBeTruthy();
    expect(summary.getByText("$40,000")).toBeTruthy();
    expect(summary.getByText("Est. Realized P&L:")).toBeTruthy();
    expect(summary.getByText("$15,000")).toBeTruthy();
    expect(summary.queryByText("Max Gain:")).toBeNull();
    expect(summary.queryByText("Max Loss:")).toBeNull();
    expect(summary.queryByText("$1,035,835")).toBeNull();
    expect(summary.queryByText("$4,165")).toBeNull();
  });

  // TEST_AUDIT T-125: bc08e87b's one-line wiring (exclude the order being
  // modified from the working-SELL count) was guarded only at the pure
  // helper; every modal render passed no `openOrders`, so dropping the
  // exclude argument stayed green while a full-size modify against 250
  // held read as a fresh opening credit spread.
  it("excludes the order being modified from the working-SELL count", () => {
    const order = cbrsComboOrder();
    render(
      <ModifyOrderModal
        order={order}
        loading={false}
        prices={cbrsComboPrices()}
        portfolio={cbrsHeldReversal()}
        openOrders={{ open_orders: [order] }}
        onConfirm={vi.fn()}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/New Net Price/i), { target: { value: "8" } });
    const summary = within(document.querySelector(".order-confirm-summary") as HTMLElement);

    expect(summary.getByText("Est. Realized P&L:")).toBeTruthy();
    expect(summary.getByText("$15,000")).toBeTruthy();
    expect(summary.queryByText("Max Loss:")).toBeNull();
    expect(summary.queryByText("Max Gain:")).toBeNull();
  });

  it("still counts a DIFFERENT working SELL combo against the held units", () => {
    const sibling = { ...cbrsComboOrder(), orderId: 89, permId: 1857172000 };
    render(
      <ModifyOrderModal
        order={cbrsComboOrder()}
        loading={false}
        prices={cbrsComboPrices()}
        portfolio={cbrsHeldReversal()}
        openOrders={{ open_orders: [cbrsComboOrder(), sibling] }}
        onConfirm={vi.fn()}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/New Net Price/i), { target: { value: "8" } });
    const summary = within(document.querySelector(".order-confirm-summary") as HTMLElement);

    expect(summary.queryByText("Est. Realized P&L:")).toBeNull();
    expect(summary.getByText("Max Loss:")).toBeTruthy();
  });

  it("scales combo close basis for a partial quantity", () => {
    render(
      <ModifyOrderModal
        order={cbrsComboOrder()}
        loading={false}
        prices={cbrsComboPrices()}
        portfolio={cbrsHeldReversal()}
        onConfirm={vi.fn()}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/New Quantity/i), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText(/New Net Price/i), { target: { value: "8" } });
    const summary = within(document.querySelector(".order-confirm-summary") as HTMLElement);

    expect(summary.getByText("Close Credit:")).toBeTruthy();
    expect(summary.getByText("$20,000")).toBeTruthy();
    expect(summary.getByText("$7,500")).toBeTruthy();
    expect(summary.queryByText("Max Gain:")).toBeNull();
  });

  it("does not treat an oversized combo SELL as a pure close", () => {
    render(
      <ModifyOrderModal
        order={cbrsComboOrder()}
        loading={false}
        prices={cbrsComboPrices()}
        portfolio={cbrsHeldReversal()}
        onConfirm={vi.fn()}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/New Quantity/i), { target: { value: "51" } });
    fireEvent.change(screen.getByLabelText(/New Net Price/i), { target: { value: "8" } });
    const summary = within(document.querySelector(".order-confirm-summary") as HTMLElement);

    expect(summary.queryByText("Est. Realized P&L:")).toBeNull();
    expect(summary.getByText("Max Loss:")).toBeTruthy();
    expect(summary.getByText("UNBOUNDED")).toBeTruthy();
  });

  it("still submits an oversized combo modify after a strike edit", () => {
    const onConfirm = vi.fn();
    render(
      <ModifyOrderModal
        order={cbrsComboOrder()}
        loading={false}
        prices={cbrsComboPrices()}
        portfolio={cbrsHeldReversal()}
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/New Quantity/i), { target: { value: "75" } });
    fireEvent.change(document.getElementById("modify-leg-1-strike") as HTMLInputElement, {
      target: { value: "210" },
    });
    fireEvent.change(screen.getByLabelText(/New Net Price/i), { target: { value: "0.75" } });

    const modify = screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;
    expect(modify.disabled).toBe(false);
    fireEvent.click(modify);
    expect(onConfirm).toHaveBeenCalled();
  });
});
