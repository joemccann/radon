/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import PositionTab from "../components/ticker-detail/PositionTab";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

const POSITION: PortfolioPosition = {
  id: 7,
  ticker: "MU",
  structure: "Risk Reversal (P$800.0/C$1050.0)",
  structure_type: "Risk Reversal",
  direction: "COMBO",
  contracts: 5,
  expiry: "2026-07-17",
  entry_date: "2026-05-29",
  entry_cost: -3495,
  market_value: -46290,
  legs: [
    { direction: "SHORT", type: "Call", strike: 1050, contracts: 3, avg_cost: 10999, entry_cost: -32997, market_price: 133.93, market_price_is_calculated: false },
    { direction: "LONG", type: "Put", strike: 800, contracts: 5, avg_cost: 5900, entry_cost: 29500, market_price: 41.0, market_price_is_calculated: false },
  ],
} as unknown as PortfolioPosition;

function optPrice(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol, last: (bid + ask) / 2, lastIsCalculated: false, bid, ask, bidSize: 1, askSize: 1,
    volume: 1, high: null, low: null, open: null, close: null, week52High: null, week52Low: null,
    avgVolume: null, delta: null, gamma: null, theta: null, vega: null, impliedVol: null,
    undPrice: null, timestamp: new Date().toISOString(),
  };
}

const PRICES: Record<string, PriceData> = {
  MU_20260717_1050_C: optPrice("MU_20260717_1050_C", 130, 134),
  MU_20260717_800_P: optPrice("MU_20260717_800_P", 40, 42),
};

const PORTFOLIO: PortfolioData = {
  bankroll: 100_000, peak_value: 100_000, last_sync: new Date().toISOString(),
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 1, defined_risk_count: 0, undefined_risk_count: 1,
  avg_kelly_optimal: null, positions: [POSITION],
} as unknown as PortfolioData;

function renderTab() {
  return render(
    React.createElement(
      OrderActionsProvider,
      null,
      React.createElement(PositionTab, { position: POSITION, prices: PRICES, portfolio: PORTFOLIO }),
    ),
  );
}

describe("PositionTab — trade affordances", () => {
  afterEach(() => cleanup());

  it("shows a Close/Adjust Combo CTA + a per-leg trade button for each leg", () => {
    renderTab();
    expect(screen.getByTestId("pos-trade-combo")).toBeTruthy();
    expect(screen.getByTestId("pos-leg-trade-0").textContent).toBe("BUY"); // short call → buy to close
    expect(screen.getByTestId("pos-leg-trade-1").textContent).toBe("SELL"); // long put → sell to close
  });

  it("uses portfolio reporting contracts once when valuing a ratio spread", () => {
    renderTab();
    const marketValue = screen.getByText("Market Value").parentElement?.textContent ?? "";
    expect(marketValue).toContain("$19,100");
    const mark = screen.getByText("Mark Price").parentElement?.textContent ?? "";
    expect(mark).toContain("-$38.20");
  });

  it("opens the combo ticket on the Close/Adjust Combo CTA", () => {
    renderTab();
    fireEvent.click(screen.getByTestId("pos-trade-combo"));
    const ticket = screen.getByTestId("position-trade-ticket");
    expect(ticket).toBeTruthy();
    expect(ticket.textContent).toContain("Trade Combo");
    expect(ticket.textContent).toContain(POSITION.structure);
    // Default action closes the combo → SELL is active.
    expect(screen.getByRole("button", { name: "SELL" }).className).toContain("order-action-sell");
  });

  it("opens the leg ticket defaulting to the closing action (BUY for the short call)", () => {
    renderTab();
    fireEvent.click(screen.getByTestId("pos-leg-trade-0"));
    const ticket = screen.getByTestId("position-trade-ticket");
    expect(ticket.textContent).toContain("Trade Leg");
    expect(ticket.textContent).toContain("SHORT Call $1050");
    // Closing a short → BUY active; qty defaults to the held 3.
    expect(screen.getByRole("button", { name: "BUY" }).className).toContain("order-action-buy");
    expect((screen.getByTestId("position-trade-qty") as HTMLInputElement).value).toBe("3");
  });

  it("accepts a NEGATIVE net limit (credit) for the combo and shows the close summary", () => {
    renderTab();
    fireEvent.click(screen.getByTestId("pos-trade-combo"));
    // Credit combos close at a negative net limit — must be valid.
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "-90.83" } });
    const review = screen.getByRole("button", { name: /review order/i }) as HTMLButtonElement;
    expect(review.disabled).toBe(false);
    fireEvent.click(review);
    // Confirm step renders the OrderRiskGate close-out summary (proceeds + P&L).
    expect(screen.getByRole("button", { name: /confirm order/i })).toBeTruthy();
    const ticket = screen.getByTestId("position-trade-ticket");
    expect(/Realized P&L|Close Credit|Close Debit/i.test(ticket.textContent || "")).toBe(true);
  });

  it("rejects a negative limit for a single leg (premiums are positive)", () => {
    renderTab();
    fireEvent.click(screen.getByTestId("pos-leg-trade-1")); // long put
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "-5" } });
    expect((screen.getByRole("button", { name: /review order/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("returns to the position view on cancel", () => {
    renderTab();
    fireEvent.click(screen.getByTestId("pos-trade-combo"));
    expect(screen.queryByTestId("position-trade-ticket")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /cancel trade/i }));
    expect(screen.queryByTestId("position-trade-ticket")).toBeNull();
    expect(screen.getByTestId("pos-trade-combo")).toBeTruthy();
  });
});
