/**
 * @vitest-environment jsdom
 *
 * R-339 / REL-122: leg quantity must be visible whether or not Qty is.
 *
 * f0335f0b moved the per-leg contract count out of the leg description and
 * into the Qty cell, which renders only when `columns.qty` is true. Column
 * visibility is user-controlled and PERSISTED, so with Qty hidden a 1x2 ratio
 * spread renders as `SHORT Put $180` / `LONG Put $175` — byte-identical to a
 * 1x1 vertical, and an uncovered ratio reads as defined risk. The same
 * collapse happens when `descColumn === "qty"`: the description displaces the
 * count in the one cell that was carrying it.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import PositionTable, {
  POSITION_COLUMN_DEFAULTS,
  type PositionColumnVisibility,
} from "../components/PositionTable";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);
beforeEach(() => window.localStorage.clear());

function makeVisibility(
  overrides: Partial<PositionColumnVisibility> = {},
): PositionColumnVisibility {
  return { ...POSITION_COLUMN_DEFAULTS, ...overrides } as PositionColumnVisibility;
}

function spread(shortQty: number): PortfolioPosition {
  return {
    id: 7,
    ticker: "SPY",
    structure: shortQty === 1 ? "Bear Put Spread $180.0/$175.0" : "Put Ratio $180.0/$175.0",
    structure_type: shortQty === 1 ? "Bear Put Spread" : "Put Ratio Spread",
    risk_profile: shortQty === 1 ? "defined" : "undefined",
    expiry: "2099-05-01",
    contracts: 1,
    direction: "COMBO",
    entry_cost: 1000,
    max_risk: 1000,
    market_value: 1200,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-04-15",
    legs: [
      {
        direction: "SHORT", contracts: shortQty, type: "Put", strike: 180,
        entry_cost: -2000 * shortQty, avg_cost: -2000,
        market_price: 18, market_value: -1800 * shortQty,
      },
      {
        direction: "LONG", contracts: 1, type: "Put", strike: 175,
        entry_cost: 1000, avg_cost: 1000, market_price: 12, market_value: 1200,
      },
    ],
  } as unknown as PortfolioPosition;
}

function renderLegText(pos: PortfolioPosition, columns: PositionColumnVisibility): string {
  const { container } = render(
    <PositionTable positions={[pos]} prices={{}} columnVisibility={columns} />,
  );
  fireEvent.click(screen.getByLabelText("Expand legs for SPY"));
  const rows = Array.from(container.querySelectorAll("tr"));
  return rows
    .filter((r) => (r.textContent ?? "").includes("Put $18") || (r.textContent ?? "").includes("Put $17"))
    .map((r) => r.textContent ?? "")
    .join("|");
}

describe("PositionTable leg quantity with Qty hidden", () => {
  it("distinguishes a 1x2 ratio from a 1x1 vertical when Qty is hidden", () => {
    const columns = makeVisibility({ qty: false });
    const ratio = renderLegText(spread(2), columns);
    cleanup();
    const vertical = renderLegText(spread(1), columns);

    expect(ratio).not.toBe(vertical);
    expect(ratio).toMatch(/2x/);
  });

  it("distinguishes them when the description displaces the Qty cell", () => {
    // structure + direction hidden -> descColumn resolves to "qty", and the
    // description takes the one cell that was carrying the count.
    const columns = makeVisibility({ structure: false, direction: false });
    const ratio = renderLegText(spread(2), columns);
    cleanup();
    const vertical = renderLegText(spread(1), columns);

    expect(ratio).not.toBe(vertical);
    expect(ratio).toMatch(/2x/);
  });

  it("does not duplicate the count when the Qty column is rendering it", () => {
    const columns = makeVisibility();
    const { container } = render(
      <PositionTable positions={[spread(2)]} prices={{}} columnVisibility={columns} />,
    );
    fireEvent.click(screen.getByLabelText("Expand legs for SPY"));

    // Description keeps its original shape ...
    expect(screen.getByText("SHORT Put $180")).toBeTruthy();
    // ... and the count is still in the Qty cell, not repeated inline.
    const header = screen.getByText("Qty").closest("th")!;
    const qtyIndex = Array.from(header.parentElement!.children).indexOf(header);
    const legRow = screen.getByText("SHORT Put $180").closest("tr")!;
    expect(Array.from(legRow.children)[qtyIndex]!.textContent).toBe("2");
    expect(container.textContent).not.toMatch(/2x Put/);
  });
});
