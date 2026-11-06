/* @vitest-environment jsdom */
/**
 * The Unrealized P&L modal states SUM( market_value − entry_cost ) per position,
 * yet printed IB's account figure as its total. 2026-11-06 screenshot: rows sum
 * to −$72,541.21 under a TOTAL of −$106,060.09.
 */
import { afterEach, describe, expect, test } from "vitest";
import React from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import MetricCards from "../components/MetricCards";

afterEach(cleanup);

const row = (ticker: string, entry_cost: number, market_value: number) => ({
  id: ticker,
  ticker,
  structure: `${ticker} Stock`,
  structure_type: "Stock",
  direction: "LONG",
  contracts: 100,
  entry_cost,
  market_value,
  entry_date: "2026-10-01",
  ib_daily_pnl: null,
  legs: [
    {
      direction: "LONG",
      contracts: 100,
      type: "Stock",
      strike: 0,
      entry_cost,
      avg_cost: entry_cost / 100,
      market_price: market_value / 100,
      market_value,
    },
  ],
});

function openModal(unrealized_pnl: number): Element {
  const portfolio = {
    bankroll: 573_523.66,
    total_deployed_dollars: 100_000,
    last_sync: "2026-11-06T18:44:07Z",
    positions: [row("WULF", 40_076.51, 3_465), row("SPCX", 37_560.85, 53_925)],
    account_summary: {
      net_liquidation: 573_523.66,
      daily_pnl: 0,
      unrealized_pnl,
      realized_pnl: 0,
      settled_cash: 0,
      maintenance_margin: 0,
      excess_liquidity: 0,
      buying_power: 0,
      dividends: 0,
    },
  };
  const { container } = render(
    React.createElement(MetricCards, {
      portfolio,
      prices: {},
      realizedPnl: 0,
      section: "portfolio",
    } as never),
  );
  const card = Array.from(container.querySelectorAll(".metric-label"))
    .find((el) => el.textContent?.trim() === "Unrealized P&L")!
    .closest(".metric-card")!;
  fireEvent.click(card);
  return document.querySelector(".modal-backdrop")!;
}

const ROW_SUM = "-$20,247.36";

describe("Unrealized P&L modal total", () => {
  test("header and TOTAL row are the sum of the rows, not IB's account figure", () => {
    const modal = openModal(-53_766.24);
    expect(modal.querySelector(".eb-total-value")?.textContent).toBe(ROW_SUM);
    const totalRow = modal.querySelector(".pb-total-label")!.closest("tr")!;
    expect(totalRow.textContent).toContain(ROW_SUM);
    expect(totalRow.textContent).not.toContain("53,766");
  });

  test("a disagreement with IB's account figure is shown with its size", () => {
    const modal = openModal(-53_766.24);
    expect(modal.querySelector(".eb-total-detail")?.textContent).toBe(
      "IB account: -$53,766.24 · difference -$33,518.88 (IB marks and cost basis)",
    );
  });

  test("agreement to the dollar prints no reconciliation line", () => {
    const modal = openModal(-20_247.1);
    expect(modal.querySelector(".eb-total-detail")).toBeNull();
  });
});
