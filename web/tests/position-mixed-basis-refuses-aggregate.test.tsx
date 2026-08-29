/**
 * @vitest-environment jsdom
 *
 * T-253: a position whose legs disagree about their basis source must not
 * present a blended aggregate as fact.
 *
 * `ib_sync._position_basis_source` stamps `basis_source: "mixed"` when SOME
 * legs carry this session's fill VWAP and others still carry IB's lagged
 * avgCost — roll the short leg of a debit vertical intraday and hold the long
 * leg overnight and that is exactly what ships. Summing those legs yields a
 * net debit for a trade that was never placed, and `resolveReturnCapital`
 * hands that number to Gate 3 as the denominator the 2.5% bankroll cap is
 * sized off.
 *
 * The server already refuses to publish the aggregate (`entry_cost` and
 * `max_risk` arrive `null`). The display layer must refuse too, because
 * `resolveEntryCost` recomputes the same blend from the legs.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import PositionTable from "../components/PositionTable";
import PositionTab from "../components/ticker-detail/PositionTab";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import { getPnlCapital, resolveReturnCapital } from "../lib/positionUtils";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);

/**
 * META Aug-28 575/580 call debit spread. The long 575C is still on IB's
 * lagged avgCost ($5.00/share → $5,000); the short 580C was rolled this
 * session and took today's VWAP ($4.00/share → $4,000). Σ = a $1,000 net
 * debit that corresponds to no actual trade.
 */
function partiallyRolledVertical(
  basisSource: PortfolioPosition["basis_source"],
  overrides: Partial<PortfolioPosition> = {},
): PortfolioPosition {
  return {
    id: 1,
    ticker: "META",
    structure: "Call Debit Spread $575.0/$580.0",
    structure_type: "Call Debit Spread",
    risk_profile: "defined",
    expiry: "2026-08-28",
    contracts: 10,
    direction: "DEBIT",
    entry_cost: null,
    max_risk: null,
    market_value: null,
    basis_source: basisSource,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-08-27",
    legs: [
      { direction: "LONG", contracts: 10, type: "Call", strike: 575,
        entry_cost: 5000, avg_cost: 500, basis_source: "ib",
        market_price: null, market_value: null },
      { direction: "SHORT", contracts: 10, type: "Call", strike: 580,
        entry_cost: 4000, avg_cost: 400, basis_source: "session_fills",
        market_price: null, market_value: null },
    ],
    ...overrides,
  } as unknown as PortfolioPosition;
}

const MIXED = partiallyRolledVertical("mixed");

function portfolioOf(pos: PortfolioPosition): PortfolioData {
  return {
    bankroll: 100_000, peak_value: 100_000, last_sync: new Date().toISOString(),
    total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
    position_count: 1, defined_risk_count: 1, undefined_risk_count: 0,
    avg_kelly_optimal: null, positions: [pos],
  } as unknown as PortfolioData;
}

describe("resolveReturnCapital refuses a blended leg basis", () => {
  it("returns no denominator for a `mixed` position", () => {
    expect(resolveReturnCapital(MIXED)).toBeNull();
  });

  it("getPnlCapital is null, so Gate 3 sizes off nothing", () => {
    expect(getPnlCapital(MIXED)).toBeNull();
  });

  it("still resolves the denominator when every leg agrees", () => {
    const clean = partiallyRolledVertical("session_fills", { max_risk: 1000 });
    expect(resolveReturnCapital(clean)).toMatchObject({
      amount: 1000,
      kind: "max-risk",
    });
  });
});

describe("PositionTab renders no basis for a `mixed` position", () => {
  function renderTab(pos: PortfolioPosition) {
    return render(
      React.createElement(
        OrderActionsProvider,
        null,
        React.createElement(PositionTab, {
          position: pos, prices: {}, portfolio: portfolioOf(pos),
        }),
      ),
    );
  }

  function statValue(label: string): string {
    const el = Array.from(document.querySelectorAll(".pos-stat")).find(
      (n) => n.querySelector(".pos-stat-label")?.textContent?.trim() === label,
    );
    return el?.querySelector(".pos-stat-value")?.textContent?.trim() ?? "";
  }

  it("Entry Cost reads --- rather than the $1,000 blend", () => {
    renderTab(MIXED);
    expect(statValue("Entry Cost")).toBe("---");
  });

  it("Avg Entry reads --- rather than the per-contract blend", () => {
    renderTab(MIXED);
    expect(statValue("Avg Entry")).toBe("---");
  });

  it("still prints the basis when every leg agrees", () => {
    renderTab(partiallyRolledVertical("session_fills", { max_risk: 1000 }));
    expect(statValue("Entry Cost")).toBe("$1,000");
  });
});

describe("PositionTable renders no basis for a `mixed` position", () => {
  function cellTexts(): string[] {
    const tr = screen.getByText("META").closest("tr")!;
    return Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim() ?? "");
  }

  it("no Entry Cost / Initial Value / Avg Entry cell carries the blend", () => {
    render(<PositionTable positions={[MIXED]} prices={{}} />);
    expect(cellTexts()).not.toContain("$1,000");
    expect(cellTexts()).not.toContain("-$1,000");
  });

  it("still prints the basis when every leg agrees", () => {
    render(<PositionTable positions={[partiallyRolledVertical("session_fills", { max_risk: 1000 })]} prices={{}} />);
    expect(cellTexts()).toContain("$1,000");
  });
});
