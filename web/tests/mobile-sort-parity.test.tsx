/**
 * @vitest-environment jsdom
 *
 * P2 (tasks/mobile-parity-audit.md): mobile sort chips exposed only a subset
 * of desktop sort keys on orders/journal/cta/discover. These tests pin the
 * chip sets against the desktop key unions and exercise the new sorts.
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MOBILE_SORT_OPTIONS } from "../components/SortableCtaTable";
import { DISCOVER_MOBILE_SORT_KEYS } from "../components/WorkspaceSections";
import MobileJournalList, { JOURNAL_SORT_CHIPS } from "../components/mobile/MobileJournalList";
import MobileOrderList, { ORDER_SORT_CHIPS } from "../components/mobile/MobileOrderList";
import type { TradeEntry } from "../lib/types";
import type { OpenOrderDisplayRow } from "../lib/openOrderCombos";

vi.mock("next/navigation", () => ({
  usePathname: () => "/journal",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
}));

afterEach(cleanup);

describe("mobile sort chip parity", () => {
  it("CTA mobile chips cover every desktop CtaSortKey", () => {
    const keys = MOBILE_SORT_OPTIONS.map((o) => o.key).sort();
    expect(keys).toEqual(
      [
        "position_today",
        "position_yesterday",
        "position_1m_ago",
        "percentile_1m",
        "percentile_3m",
        "percentile_1y",
        "z_score_3m",
      ].sort(),
    );
  });

  it("Discover mobile chips cover every desktop DiscoverSortKey", () => {
    const keys = DISCOVER_MOBILE_SORT_KEYS.map((o) => o.key).sort();
    expect(keys).toEqual(
      [
        "ticker",
        "score",
        "dp_direction",
        "dp_strength",
        "dp_buy_ratio",
        "options_bias",
        "alerts",
        "total_premium",
        "sweeps",
        "sector",
      ].sort(),
    );
  });

  it("Journal mobile chips expose the meaningful desktop keys", () => {
    const keys = JOURNAL_SORT_CHIPS.map((o) => o.key).sort();
    expect(keys).toEqual(["date", "ticker", "pnl", "ror", "cost", "risk"].sort());
  });

  it("Orders mobile chips expose the meaningful desktop keys", () => {
    const keys = ORDER_SORT_CHIPS.map((o) => o.key);
    for (const required of ["symbol", "totalQuantity", "limitPrice", "status"]) {
      expect(keys).toContain(required);
    }
  });
});

function trade(overrides: Partial<TradeEntry>): TradeEntry {
  return {
    id: 1,
    date: "2026-06-01",
    ticker: "AAA",
    structure: "Long Call",
    decision: "CLOSED",
    entry_cost: 100,
    max_risk: 100,
    realized_pnl: 0,
    return_on_risk: 0,
    ...overrides,
  } as TradeEntry;
}

describe("MobileJournalList new sorts", () => {
  it("ticker chip sorts alphabetically", () => {
    render(
      <MobileJournalList
        trades={[
          trade({ id: 1, ticker: "ZM", date: "2026-06-30" }),
          trade({ id: 2, ticker: "AAPL", date: "2026-06-01" }),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Ticker" }));

    const list = screen.getByTestId("mobile-journal-list");
    expect(list.textContent!.indexOf("AAPL")).toBeLessThan(list.textContent!.indexOf("ZM"));
  });

  it("cost chip sorts by entry cost descending", () => {
    render(
      <MobileJournalList
        trades={[
          trade({ id: 1, ticker: "SMALL", entry_cost: 50 }),
          trade({ id: 2, ticker: "BIG", entry_cost: 5000 }),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cost" }));

    const list = screen.getByTestId("mobile-journal-list");
    expect(list.textContent!.indexOf("BIG")).toBeLessThan(list.textContent!.indexOf("SMALL"));
  });
});

function singleRow(symbol: string, limitPrice: number, status: string): OpenOrderDisplayRow {
  return {
    kind: "single",
    index: 0,
    summary: null,
    order: {
      permId: Math.round(limitPrice * 1000),
      orderId: 1,
      clientId: 0,
      action: "BUY",
      totalQuantity: 1,
      orderType: "LMT",
      limitPrice,
      tif: "DAY",
      status,
      contract: { symbol, secType: "STK", strike: null, right: null, expiry: null },
    },
  } as unknown as OpenOrderDisplayRow;
}

describe("MobileOrderList sortbar", () => {
  const noop = () => undefined;

  it("renders sort chips and limit sort reorders rows", () => {
    render(
      <MobileOrderList
        rows={[singleRow("LOW", 5, "Submitted"), singleRow("HIGH", 500, "Submitted")]}
        canModify={() => false}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Limit" }));

    const list = screen.getByTestId("mobile-order-list");
    expect(list.textContent!.indexOf("HIGH")).toBeLessThan(list.textContent!.indexOf("LOW"));
  });
});
