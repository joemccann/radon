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
 *
 * T-315: the T-253 fixture carried no leg marks, so `resolveMarketValue` was
 * null and every P&L branch was unreachable — the cells it did not fix (P&L,
 * the portfolio Open P&L total, the unrealized breakdown, Today P&L and the
 * close ticket's realised figure) still subtracted the blend. The fixture
 * now marks both legs (mv = $4,500) so a blended P&L of +$3,500 is exactly
 * what an ungated path prints.
 */

import React from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { cleanup as cleanupHooks, renderHook } from "@testing-library/react";

import MetricCards from "../components/MetricCards";
import PositionTable from "../components/PositionTable";
import PositionTab from "../components/ticker-detail/PositionTab";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import { OrderConfirmSummary } from "../lib/order/components/OrderConfirmSummary";
import { buildPositionTradeOrder } from "../lib/order/positionTrade";
import { useOrderRisk } from "../lib/order/risk";
import {
  getPnlCapital,
  getPnlDollars,
  getTodayPnlDollars,
  resolveEntryCost,
  resolveReturnCapital,
} from "../lib/positionUtils";
import {
  computeUnrealizedBreakdown,
  countUnmeasuredBasis,
  sumUnrealizedBreakdown,
} from "../lib/unrealizedBreakdown";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));
vi.mock("@/lib/useMarketHours", () => ({
  useMarketHours: () => ({ state: "closed", isOpen: false }),
}));

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
  cleanup();
  cleanupHooks();
});

/**
 * META Aug-28 575/580 call debit spread. The long 575C is still on IB's
 * lagged avgCost ($5.00/share → $5,000); the short 580C was rolled this
 * session and took today's VWAP ($4.00/share → $4,000). Σ = a $1,000 net
 * debit that corresponds to no actual trade.
 *
 * Marks: long 575C $7.50 (+$7,500), short 580C $3.00 (−$3,000) → mv $4,500,
 * so the blended P&L an ungated path prints is exactly +$3,500.
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
        market_price: 7.5, market_value: 7500 },
      { direction: "SHORT", contracts: 10, type: "Call", strike: 580,
        entry_cost: 4000, avg_cost: 400, basis_source: "session_fills",
        market_price: 3.0, market_value: -3000 },
    ],
    ...overrides,
  } as unknown as PortfolioPosition;
}

function todayET(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

const MIXED = partiallyRolledVertical("mixed");
const CLEAN = partiallyRolledVertical("session_fills", { max_risk: 1000 });
const BLENDED_PNL = "+$3,500";

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

  function cellUnder(header: string): string {
    const headers = Array.from(document.querySelectorAll("thead th")).map(
      (th) => th.textContent?.trim() ?? "",
    );
    const idx = headers.findIndex((h) => h.startsWith(header));
    expect(idx).toBeGreaterThanOrEqual(0);
    return cellTexts()[idx];
  }

  it("no Entry Cost / Initial Value / Avg Entry cell carries the blend", () => {
    render(<PositionTable positions={[MIXED]} prices={{}} />);
    expect(cellTexts()).not.toContain("$1,000");
    expect(cellTexts()).not.toContain("-$1,000");
  });

  it("the P&L cell reads — and Return % N/A rather than mv − blend (T-315)", () => {
    render(<PositionTable positions={[MIXED]} prices={{}} />);
    expect(cellTexts()).not.toContain(BLENDED_PNL);
    expect(cellUnder("P&L")).toBe("—");
    expect(cellUnder("Return %")).toBe("N/A");
  });

  it("still prints the P&L when every leg agrees", () => {
    render(<PositionTable positions={[CLEAN]} prices={{}} />);
    expect(cellUnder("P&L")).toBe(BLENDED_PNL);
  });

  it("still prints the basis when every leg agrees", () => {
    render(<PositionTable positions={[partiallyRolledVertical("session_fills", { max_risk: 1000 })]} prices={{}} />);
    expect(cellTexts()).toContain("$1,000");
  });
});

describe("resolveEntryCost / getPnlDollars refuse a blended leg basis (T-315)", () => {
  it("resolveEntryCost is null for a `mixed` position", () => {
    expect(resolveEntryCost(MIXED)).toBeNull();
    expect(resolveEntryCost(CLEAN)).toBe(1000);
  });

  it("getPnlDollars is null even with a real mark", () => {
    expect(getPnlDollars(MIXED, 4500)).toBeNull();
    expect(getPnlDollars(CLEAN, 4500)).toBe(3500);
  });

  it("Today P&L for a same-day `mixed` position is null, not mv − blend", () => {
    const sameDay = partiallyRolledVertical("mixed", { entry_date: todayET() });
    expect(getTodayPnlDollars(sameDay, {})).toBeNull();
    const sameDayClean = partiallyRolledVertical("session_fills", { entry_date: todayET() });
    expect(getTodayPnlDollars(sameDayClean, {})).toBe(3500);
  });
});

describe("unrealized breakdown omits a `mixed` position and counts it unmeasured (T-315)", () => {
  it("computeUnrealizedBreakdown emits no row for it", () => {
    expect(computeUnrealizedBreakdown(portfolioOf(MIXED))).toEqual([]);
    expect(computeUnrealizedBreakdown(portfolioOf(CLEAN))).toHaveLength(1);
  });

  it("sumUnrealizedBreakdown excludes it and countUnmeasuredBasis names it", () => {
    const both = { ...portfolioOf(CLEAN), positions: [CLEAN, { ...MIXED, id: 2 }] };
    expect(sumUnrealizedBreakdown(both)).toBe(3500);
    expect(countUnmeasuredBasis(both)).toBe(1);
    expect(countUnmeasuredBasis(portfolioOf(CLEAN))).toBe(0);
  });
});

describe("MetricCards Open P&L excludes a `mixed` position (T-315)", () => {
  function openPnlCard(): HTMLElement {
    const label = Array.from(document.querySelectorAll(".metric-label")).find(
      (n) => n.textContent?.trim() === "Open P&L",
    );
    return label!.closest(".metric-card") as HTMLElement;
  }

  it("totals only the measured position and labels the unmeasured one", () => {
    const both = { ...portfolioOf(CLEAN), positions: [CLEAN, { ...MIXED, id: 2 }] };
    render(<MetricCards portfolio={both} prices={{}} section="portfolio" />);
    const card = openPnlCard();
    expect(card.querySelector(".metric-value")?.textContent).toBe(BLENDED_PNL);
    expect(card.querySelector(".metric-change")?.textContent).toContain("1 UNMEASURED BASIS");
  });

  it("carries no unmeasured label when every leg agrees", () => {
    render(<MetricCards portfolio={portfolioOf(CLEAN)} prices={{}} section="portfolio" />);
    const card = openPnlCard();
    expect(card.querySelector(".metric-value")?.textContent).toBe(BLENDED_PNL);
    expect(card.querySelector(".metric-change")?.textContent).not.toContain("UNMEASURED");
  });
});

describe("close ticket carries no realised figure for a `mixed` position (T-315)", () => {
  function comboClose(pos: PortfolioPosition) {
    return buildPositionTradeOrder({
      position: pos,
      target: { kind: "combo" },
      action: "SELL",
      quantity: 10,
      limitPrice: 4.5,
      tif: "DAY",
    })!;
  }

  it("buildPositionTradeOrder marks the close-out basis unavailable", () => {
    const o = comboClose(MIXED);
    expect(o.isClosing).toBe(true);
    expect(o.riskInput.closeOut).not.toBeNull();
    expect(o.riskInput.closeOut?.entryCostDollars).toBeNull();
    expect(comboClose(CLEAN).riskInput.closeOut?.entryCostDollars).toBe(1000);
  });

  it("useOrderRisk yields estimatedPnl null and the ticket prints no Est. Realized P&L", () => {
    const o = comboClose(MIXED);
    const { result } = renderHook(() => useOrderRisk(o.riskInput, portfolioOf(MIXED)));
    expect(result.current!.summary.estimatedPnl).toBeNull();
    expect(result.current!.okToSubmit).toBe(true);
    render(<OrderConfirmSummary summary={result.current!.summary} />);
    expect(document.body.textContent).not.toContain("Est. Realized P&L");
    expect(document.body.textContent).not.toContain("3,500");
  });

  it("still reports the realised figure when every leg agrees", () => {
    const o = comboClose(CLEAN);
    const { result } = renderHook(() => useOrderRisk(o.riskInput, portfolioOf(CLEAN)));
    expect(result.current!.summary.estimatedPnl).toBe(3500);
  });
});
