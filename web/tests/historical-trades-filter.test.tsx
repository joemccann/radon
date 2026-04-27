/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { BlotterData } from "../lib/types";
import { HistoricalTradesSection } from "../components/WorkspaceSections";

vi.mock("../components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));

const useBlotterMock = vi.fn();

vi.mock("../lib/useBlotter", () => ({
  useBlotter: (...args: unknown[]) => useBlotterMock(...args),
}));

const BASE_BLOTTER: BlotterData = {
  as_of: new Date("2026-03-25T17:00:00.000Z").toISOString(),
  summary: {
    closed_trades: 2,
    open_trades: 1,
    total_commissions: 7.8,
    realized_pnl: 180,
  },
  closed_trades: [
    {
      symbol: "AAPL",
      contract_desc: "AAPL 20260320 180C",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_commission: 2.5,
      realized_pnl: 120,
      cost_basis: 1200,
      proceeds: 1320,
      total_cash_flow: 120,
      executions: [
        {
          exec_id: "e1",
          time: "2026-03-24T10:10:00.000Z",
          side: "SLD",
          quantity: 1,
          price: 13.20,
          commission: 1.25,
          notional_value: 1320,
          net_cash_flow: -1318.75,
        },
      ],
    },
    {
      symbol: "MSFT",
      contract_desc: "MSFT 20260320 350P",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_commission: 2.6,
      realized_pnl: 60,
      cost_basis: 900,
      proceeds: 960,
      total_cash_flow: 60,
      executions: [
        {
          exec_id: "e2",
          time: "2026-03-23T11:15:00.000Z",
          side: "SLD",
          quantity: 1,
          price: 9.6,
          commission: 1.3,
          notional_value: 960,
          net_cash_flow: -961.3,
        },
      ],
    },
  ],
  open_trades: [
    {
      symbol: "TSLA",
      contract_desc: "TSLA 20260320 250C",
      sec_type: "OPT",
      is_closed: false,
      net_quantity: 2,
      total_commission: 2.7,
      realized_pnl: 0,
      realized_quantity: 0,
      realized_cost_basis: null,
      cost_basis: 0,
      proceeds: 0,
      total_cash_flow: 0,
      executions: [
        {
          exec_id: "e3",
          time: "2026-03-22T09:40:00.000Z",
          side: "BOT",
          quantity: 2,
          price: 18,
          commission: 1.4,
          notional_value: 3600,
          net_cash_flow: 3598.6,
        },
      ],
    },
  ],
};

beforeEach(() => {
  useBlotterMock.mockReturnValue({
    data: BASE_BLOTTER,
    loading: false,
    syncing: false,
    error: null,
    syncNow: vi.fn(),
  });
});

afterEach(() => {
  useBlotterMock.mockReset();
  cleanup();
});

describe("HistoricalTradesSection", () => {
  it("renders a filter input and filters rows by symbol", () => {
    render(React.createElement(HistoricalTradesSection));

    const filter = screen.getByPlaceholderText("Filter historical trades...");
    expect(filter).toBeTruthy();

    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("MSFT")).toBeTruthy();
    expect(screen.getByText("TSLA")).toBeTruthy();

    fireEvent.change(filter, { target: { value: "AAPL" } });

    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.queryByText("MSFT")).toBeNull();
    expect(screen.queryByText("TSLA")).toBeNull();
    expect(screen.getByText("1/3")).toBeTruthy();
  });

  it("can clear filter state to restore full row set", () => {
    render(React.createElement(HistoricalTradesSection));

    const filter = screen.getByPlaceholderText("Filter historical trades...");
    fireEvent.change(filter, { target: { value: "MSFT" } });
    expect(screen.getByText("MSFT")).toBeTruthy();
    expect(screen.getByText("1/3")).toBeTruthy();

    fireEvent.change(filter, { target: { value: "" } });

    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("MSFT")).toBeTruthy();
    expect(screen.getByText("TSLA")).toBeTruthy();
    expect(screen.queryByText("1/3")).toBeNull();
  });

  it("shows realized pnl and sold quantity for partially closed open trades", () => {
    const partialBlotter: BlotterData = {
      ...BASE_BLOTTER,
      open_trades: [
        {
          symbol: "ALAB",
          contract_desc: "ALAB 20270115 120C",
          sec_type: "OPT",
          is_closed: false,
          net_quantity: 2,
          total_quantity: 5,
          total_commission: 4.93,
          realized_pnl: 17275.97,
          realized_quantity: 3,
          realized_cost_basis: 11071.34,
          cost_basis: 7380.90,
          proceeds: 28347.31,
          total_cash_flow: 9883.07,
          executions: [
            {
              exec_id: "alab-open",
              time: "2026-04-15T14:30:00.000Z",
              side: "BOT",
              quantity: 5,
              price: 36.90,
              commission: 4.93,
              notional_value: 18447.31,
              net_cash_flow: -18452.24,
            },
            {
              exec_id: "alab-close-partial",
              time: "2026-04-21T19:00:00.000Z",
              side: "SLD",
              quantity: 3,
              price: 94.51,
              commission: 0,
              notional_value: 28347.31,
              net_cash_flow: 28347.31,
            },
          ],
        },
      ],
    };

    useBlotterMock.mockReturnValue({
      data: partialBlotter,
      loading: false,
      syncing: false,
      error: null,
      syncNow: vi.fn(),
    });

    render(React.createElement(HistoricalTradesSection));

    expect(screen.getByText(/\+\$17,275\.97 \(\+156\.0%\) · 3 sold/)).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("$7,380.90")).toBeTruthy();
  });
});
