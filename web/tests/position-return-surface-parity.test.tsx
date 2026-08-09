/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import PositionTable from "../components/PositionTable";
import MobilePositionList from "../components/mobile/MobilePositionList";
import PnlBreakdownModal from "../components/PnlBreakdownModal";
import PositionTab from "../components/ticker-detail/PositionTab";
import { computeUnrealizedBreakdown } from "../lib/unrealizedBreakdown";
import { getPnlDollars, getPnlPct } from "../lib/positionUtils";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

afterEach(cleanup);

const DEFINED_CALL: PortfolioPosition = {
  id: 17,
  ticker: "GLD",
  structure: "Long Call $450",
  structure_type: "Long Call",
  risk_profile: "defined",
  expiry: "2027-01-15",
  contracts: 10,
  direction: "LONG",
  entry_cost: 5_000,
  max_risk: 10_000,
  market_value: 6_000,
  legs: [{
    direction: "LONG",
    contracts: 10,
    type: "Call",
    strike: 450,
    entry_cost: 5_000,
    avg_cost: 500,
    market_price: 6,
    market_value: 6_000,
  }],
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-08-07",
};

const PORTFOLIO = {
  positions: [DEFINED_CALL],
} as unknown as PortfolioData;

const OBSERVED_CREDIT: PortfolioPosition = {
  ...DEFINED_CALL,
  id: 18,
  account_id: "U1",
  position_instance_id: "PI-SPCX-1",
  ticker: "SPCX",
  structure: "Ratio Risk Reversal",
  structure_type: "Ratio Risk Reversal",
  risk_profile: "undefined",
  contracts: 60,
  direction: "COMBO",
  entry_cost: -1_514,
  max_risk: null,
  market_value: 26_850,
  legs: [
    { con_id: 101, direction: "LONG", contracts: 60, type: "Call", strike: 135, entry_cost: 48_429, avg_cost: 807.15, market_price: 7, market_value: 42_000 },
    { con_id: 102, direction: "SHORT", contracts: 30, type: "Put", strike: 120, entry_cost: 49_943, avg_cost: 1_664.77, market_price: 5.05, market_value: 15_150 },
  ],
  return_capital: {
    version: 2,
    amount: 40_000,
    currency: "USD",
    measurement: {
      quality: "observed",
      method: "isolated-account-margin-delta",
      measured_at: "2026-07-01T15:31:00Z",
      observation_id: "OBS-1",
      isolation: "isolated",
      before_sample_id: "S1",
      after_sample_id: "S2",
      window_seconds: 60,
      concurrent_exec_ids: [],
    },
    linkage: {
      state: "linked",
      account_id: "U1",
      position_instance_id: "PI-SPCX-1",
      con_ids: [101, 102],
      order_refs: ["radon-spcx-1"],
      perm_ids: [9001],
      exec_ids: ["E1", "E2"],
      legs: [
        { con_id: 101, currency: "USD", multiplier: 100 },
        { con_id: 102, currency: "USD", multiplier: 100 },
      ],
    },
  },
};

const ESTIMATED_CREDIT: PortfolioPosition = {
  ...OBSERVED_CREDIT,
  id: 19,
  ticker: "META",
  position_instance_id: "PI-META-1",
  return_capital: {
    version: 2,
    amount: 40_000,
    currency: "USD",
    measurement: {
      quality: "estimated",
      method: "ib-whatif",
      measured_at: "2026-07-01T15:31:00Z",
    },
    linkage: {
      ...OBSERVED_CREDIT.return_capital!.linkage,
      state: "linked",
      position_instance_id: "PI-META-1",
    },
  },
};

describe("position return surfaces use the shared basis", () => {
  it("keeps the breakdown helper in parity with positionUtils", () => {
    const row = computeUnrealizedBreakdown(PORTFOLIO)[0];
    expect(row.pnl).toBe(getPnlDollars(DEFINED_CALL));
    expect(row.pnlPct).toBe(getPnlPct(DEFINED_CALL));
    expect(row.pnlPct).toBe(10);
  });

  it("renders the same 10% max-risk return in desktop and mobile lists", () => {
    const desktop = render(<PositionTable positions={[DEFINED_CALL]} prices={{}} />);
    expect(screen.getByText("Return %")).toBeTruthy();
    expect(screen.getByText("+10.0%").getAttribute("title")).toContain("max risk");
    desktop.unmount();

    render(<MobilePositionList positions={[DEFINED_CALL]} prices={{}} />);
    expect(screen.getByText("Return %")).toBeTruthy();
    const returns = screen.getAllByText("+10.00%");
    expect(returns.some((node) => node.parentElement?.getAttribute("title")?.includes("max risk"))).toBe(true);
  });

  it("renders ticker detail return from the same helper and provenance", () => {
    render(<PositionTab position={DEFINED_CALL} prices={{}} portfolio={PORTFOLIO} />);
    expect(screen.getByText("Unrealized P&L / Return")).toBeTruthy();
    const value = screen.getByText(/\+\$1,000 \(10\.0%\)/);
    expect(value.getAttribute("title")).toContain("max risk");
  });

  it("keeps isolated observed return and provenance aligned across surfaces", () => {
    const observedPortfolio = { positions: [OBSERVED_CREDIT] } as unknown as PortfolioData;
    expect(computeUnrealizedBreakdown(observedPortfolio)[0]?.pnlPct).toBeCloseTo(70.91, 2);

    const desktop = render(<PositionTable positions={[OBSERVED_CREDIT]} prices={{}} />);
    expect(screen.getByText("+70.9%").getAttribute("title")).toContain("isolated broker-observed");
    desktop.unmount();

    const mobile = render(<MobilePositionList positions={[OBSERVED_CREDIT]} prices={{}} />);
    expect(screen.getAllByText("+70.91%").some((node) => node.parentElement?.getAttribute("title")?.includes("isolated broker-observed"))).toBe(true);
    mobile.unmount();

    render(<PositionTab position={OBSERVED_CREDIT} prices={{}} portfolio={observedPortfolio} />);
    expect(screen.getByText(/\+\$28,364 \(70\.9%\)/).getAttribute("title")).toContain("isolated broker-observed");
  });

  it("renders estimated capital as N/A across every return surface", () => {
    const estimatedPortfolio = { positions: [ESTIMATED_CREDIT] } as unknown as PortfolioData;
    const rows = computeUnrealizedBreakdown(estimatedPortfolio);
    expect(rows[0]?.pnlPct).toBeNull();

    const desktop = render(<PositionTable positions={[ESTIMATED_CREDIT]} prices={{}} />);
    const desktopRow = screen.getByText("META").closest("tr");
    expect(desktopRow).not.toBeNull();
    expect(within(desktopRow!).getByText("N/A").getAttribute("title")).toContain("Return unavailable");
    desktop.unmount();

    const mobile = render(<MobilePositionList positions={[ESTIMATED_CREDIT]} prices={{}} />);
    expect(screen.getAllByText("N/A").some((node) => node.parentElement?.getAttribute("title")?.includes("Return unavailable"))).toBe(true);
    mobile.unmount();

    const ticker = render(<PositionTab position={ESTIMATED_CREDIT} prices={{}} portfolio={estimatedPortfolio} />);
    expect(screen.getByText(/Return N\/A/).getAttribute("title")).toContain("Return unavailable");
    ticker.unmount();

    render(
      <PnlBreakdownModal
        open
        title="Unrealized P&L"
        formula="Market value - entry cost"
        col1Header="Entry Cost"
        col2Header="Mkt Value"
        pctHeader="Return %"
        rows={rows}
        total={rows[0]!.pnl}
        onClose={() => undefined}
      />,
    );
    const modalRow = screen.getByText("META").closest("tr");
    expect(modalRow).not.toBeNull();
    expect(within(modalRow!).getByText("N/A")).toBeTruthy();
  });
});
