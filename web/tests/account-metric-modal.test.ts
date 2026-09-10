/**
 * @vitest-environment jsdom
 *
 * AccountMetricModal — the ACCOUNT / RISK card drill-downs.
 *
 * T-225: this suite used to declare itself a "mirror" of MetricCards.tsx and
 * assert against its OWN local copy of the modal copy, never rendering the
 * component. An em-dash sweep rewrote the Day P&L source line to
 * "reqPnL(), account-level" and added a second, estimated-source branch; the
 * mirror kept the old spelling and the suite stayed green, because nothing
 * ever compared the two. Every assertion below now reads the text off the
 * rendered modal, so a copy edit in the component reds the test.
 */

import React from "react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import MetricCards from "../components/MetricCards";
import type { AccountSummary } from "../lib/types";

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    class Stub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: typeof Stub }).ResizeObserver = Stub;
  }
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

// ── Shared mock account summary ──────────────────────────────────────────────

const MOCK_ACCT: AccountSummary = {
  net_liquidation: 1_131_051.65,
  daily_pnl: -17_071.27,
  unrealized_pnl: -212_251.69,
  realized_pnl: -6_835.27,
  settled_cash: -14_654.04,
  maintenance_margin: 513_065.33,
  excess_liquidity: 185_943.44,
  buying_power: 743_773.78,
  dividends: 910.0,
};

type Portfolio = Parameters<typeof MetricCards>[0]["portfolio"];

function buildPortfolio(acct: AccountSummary = MOCK_ACCT): Portfolio {
  return {
    bankroll: acct.net_liquidation,
    net_leverage: 0.5,
    total_deployed_dollars: 100_000,
    total_pnl_pct: 1.0,
    positions: [
      {
        id: "AAPL-stock",
        ticker: "AAPL",
        structure: "Stock",
        structure_type: "Stock",
        direction: "LONG",
        qty: 100,
        contracts: 100,
        avg_entry: 180,
        cost: 18_000,
        legs: [],
        market_value: 19_000,
        pnl: 1_000,
        pnl_pct: 5.5,
        entry_date: "2026-04-01",
        ib_daily_pnl: null,
      },
    ],
    account_summary: acct,
  } as unknown as Portfolio;
}

/** Open a card's drill-down and read the modal title + formula off the DOM.
 *  One card per render, so exactly one modal is mounted when we read it. */
function openCard(label: string, acct: AccountSummary = MOCK_ACCT): { title: string; formula: string } {
  const { container } = render(
    React.createElement(MetricCards, {
      portfolio: buildPortfolio(acct),
      prices: { AAPL: { last: 190, close: 188 } },
      realizedPnl: acct.realized_pnl,
      section: "portfolio",
    } as unknown as Parameters<typeof MetricCards>[0]),
  );

  // RISK / MARGIN / CAPITAL rows start collapsed; open every section header so
  // any card is reachable without the test knowing which row owns it.
  for (const header of Array.from(container.querySelectorAll(".section-label-toggle"))) {
    const name = (header.textContent ?? "").trim();
    if (name === "RISK" || name === "MARGIN" || name === "CAPITAL") fireEvent.click(header);
  }

  const card = Array.from(container.querySelectorAll(".metric-label"))
    .find((el) => (el.textContent ?? "").trim() === label)
    ?.closest(".metric-card");
  expect(card, `no metric card labelled "${label}"`).toBeTruthy();
  expect(card!.className).toContain("metric-card-clickable");
  fireEvent.click(card!);

  const modal = document.querySelector(".modal-backdrop");
  expect(modal, `clicking "${label}" opened no modal`).toBeTruthy();
  return {
    title: (modal!.querySelector(".modal-title")?.textContent ?? "").trim(),
    formula: modal!.querySelector(".eb-formula code")?.textContent ?? "",
  };
}

// ── Tests: the rendered modal copy ───────────────────────────────────────────
//
// Each expectation is the WHOLE formula block, so a reworded line, a dropped
// source attribution or a punctuation sweep all fail loudly instead of
// slipping past a substring match.

describe("AccountMetricModal — rendered copy", () => {
  test("NET LIQUIDATION card", () => {
    const { title, formula } = openCard("Net Liquidation");
    expect(title).toBe("Net Liquidation Value");
    expect(formula).toBe(
      "Net Liquidation = Cash + Stocks at Market Value + Options at Market Value + Bond Value\n" +
      "Source: Interactive Brokers account_summary (reqAccountSummary)\n" +
      "Updated: real-time during market hours",
    );
  });

  test("DAY P&L card cites the IB aggregate when IB reported one", () => {
    const { title, formula } = openCard("Day P&L");
    expect(title).toBe("Day P&L");
    expect(formula).toBe(
      "Day P&L = SUM( current_price − yesterday_close ) × position_size\n" +
      "Source: Interactive Brokers reqPnL(), account-level, updated in real-time\n" +
      "Note: Includes all open positions across stocks, options, and other instruments",
    );
  });

  test("DAY P&L card owns up to the estimate when IB reported no aggregate", () => {
    const { formula } = openCard("Day P&L", { ...MOCK_ACCT, daily_pnl: null as unknown as number });
    expect(formula).toBe(
      "Day P&L = SUM( current_price − yesterday_close ) × position_size\n" +
      "Source: Current prices versus prior-session closes, estimated\n" +
      "Note: Includes all open positions across stocks, options, and other instruments",
    );
  });

  test("UNREALIZED P&L card", () => {
    const { title, formula } = openCard("Unrealized P&L");
    expect(title).toBe("Unrealized P&L: Open Positions");
    expect(formula).toBe(
      "Unrealized P&L = SUM( market_value − entry_cost ) per position\n" +
      "Entry cost and market value are signed (credits and short marks negative)\n" +
      "so each row satisfies P&L = MKT VALUE − ENTRY COST.\n" +
      "Return uses verified max loss, net debit paid, or isolated broker-observed opening margin; opening credits stay N/A.\n" +
      "Source: IB market data synced via IB Gateway",
    );
  });

  test("DAY MOVE card", () => {
    // Pin to a US session: on weekends the card is MARKET CLOSED and not
    // clickable, which failed shard 7 on 35071d85 (Saturday 2026-08-29).
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-21T18:30:00Z")); // Fri 14:30 ET
    const { title, formula } = openCard("Day Move");
    expect(title).toBe("Day Move: Intraday P&L");
    expect(formula).toBe(
      "Day Move = stocks: (last − close) × shares; options: IB reqPnLSingle daily P&L when available, else sign × (last − close) × contracts × multiplier\n" +
      "sign = +1 LONG, −1 SHORT  |  multiplier = 100 for options, 1 for stocks\n" +
      "Source: IB reqPnLSingle + live IB realtime prices",
    );
  });

  test("DIVIDENDS card", () => {
    const { title, formula } = openCard("Dividends");
    expect(title).toBe("Accrued Dividends");
    expect(formula).toBe(
      "Dividends = Accrued dividends from dividend-paying positions\n" +
      "Source: Interactive Brokers account_summary (DividendReceivedYear)\n" +
      "Note: Represents dividends accrued in the current calendar year",
    );
  });

  test("BUYING POWER card", () => {
    const { title, formula } = openCard("Buying Power");
    expect(title).toBe("Buying Power");
    expect(formula).toBe(
      "Buying Power = Available margin capacity for new positions\n" +
      "Source: Interactive Brokers account_summary (BuyingPower)\n" +
      "= Excess Liquidity × Margin Multiplier\n" +
      "Note: For a Reg T margin account, typically 4× excess liquidity for day trades",
    );
  });

  test("MAINTENANCE MARGIN card", () => {
    const { title, formula } = openCard("Maintenance Margin");
    expect(title).toBe("Maintenance Margin");
    expect(formula).toBe(
      "Maintenance Margin = Minimum equity required to maintain current positions\n" +
      "Source: Interactive Brokers account_summary (MaintMarginReq)\n" +
      "If Net Liquidation falls below this, IB may issue a margin call",
    );
  });

  test("EXCESS LIQUIDITY card", () => {
    const { title, formula } = openCard("Excess Liquidity");
    expect(title).toBe("Excess Liquidity");
    expect(formula).toBe(
      "Excess Liquidity = Net Liquidation − Maintenance Margin\n" +
      "Source: Interactive Brokers account_summary (ExcessLiquidity)\n" +
      "= Safety cushion above margin requirements\n" +
      "Green = healthy buffer | Red = dangerously close to margin call",
    );
  });

  test("SETTLED CASH card", () => {
    const { title, formula } = openCard("Settled Cash");
    expect(title).toBe("Settled Cash");
    expect(formula).toBe(
      "Settled Cash = Cash settled and available (T+1 for options, T+2 for stocks)\n" +
      "Source: Interactive Brokers account_summary (SettledCash)\n" +
      "Negative = you've spent unsettled funds (cash from recent sells not yet settled)",
    );
  });
});

// ── Tests: the value each modal reports ──────────────────────────────────────

describe("AccountMetricModal — rendered value", () => {
  const modalValue = () =>
    (document.querySelector(".modal-backdrop .eb-total-value")?.textContent ?? "").trim();

  test("NET LIQUIDATION reports the account summary figure", () => {
    openCard("Net Liquidation");
    expect(modalValue()).toContain("1,131,051.65");
  });

  test("SETTLED CASH keeps the negative sign", () => {
    openCard("Settled Cash");
    expect(modalValue()).toContain("14,654.04");
    expect(modalValue().startsWith("-")).toBe(true);
  });
});
