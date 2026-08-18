/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import FillsModal from "@/components/FillsModal";
import type { ExecutedOrder } from "@/lib/types";

afterEach(() => cleanup());

function fill(partial: Partial<ExecutedOrder> & { execId: string; symbol: string }): ExecutedOrder {
  return {
    contract: { conId: 1, symbol: partial.symbol, secType: "STK", strike: null, right: null, expiry: null },
    side: "BOT",
    quantity: 1,
    avgPrice: 100,
    commission: 1,
    realizedPNL: 0,
    time: "2026-08-08T14:00:00Z",
    exchange: "SMART",
    ...partial,
  };
}

describe("FillsModal sort", () => {
  it("reorders rows when SYMBOL is clicked", () => {
    render(
      <FillsModal
        open
        onClose={vi.fn()}
        totalRealizedPnl={0}
        fills={[
          fill({ execId: "1", symbol: "MSFT", time: "2026-08-08T14:01:00Z" }),
          fill({ execId: "2", symbol: "AAPL", time: "2026-08-08T14:02:00Z" }),
          fill({ execId: "3", symbol: "NVDA", time: "2026-08-08T14:03:00Z" }),
        ]}
      />,
    );
    const table = screen.getByRole("table");
    const symbols = () =>
      within(table)
        .getAllByRole("row")
        .slice(1)
        .map((row) => within(row).getAllByRole("cell")[1].textContent);

    expect(symbols()).toEqual(["MSFT", "AAPL", "NVDA"]);
    fireEvent.click(screen.getByRole("columnheader", { name: /symbol/i }));
    expect(symbols()).toEqual(["AAPL", "MSFT", "NVDA"]);
    expect(screen.getByRole("columnheader", { name: /symbol/i }).getAttribute("aria-sort")).toBe("ascending");
  });
});
