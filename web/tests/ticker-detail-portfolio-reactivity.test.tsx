/**
 * @vitest-environment jsdom
 *
 * NEW_FINDINGS (wave-2): setPortfolio wrote a ref only; TickerWorkspace
 * sampled getPortfolio() once per render and never re-rendered when the
 * portfolio landed after client-side navigation.
 */
import React, { useEffect } from "react";
import { cleanup, render, screen, act } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  TickerDetailProvider,
  useTickerDetail,
} from "@/lib/TickerDetailContext";
import type { PortfolioData } from "@/lib/types";

afterEach(() => cleanup());

const SAMPLE: PortfolioData = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: "2026-08-08T14:00:00Z",
  total_deployed_pct: 10,
  total_deployed_dollars: 100_000,
  remaining_capacity_pct: 90,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    {
      id: 1,
      ticker: "GLD",
      structure: "long_call",
      direction: "LONG",
      quantity: 1,
      contracts: 1,
      entry_cost: 500,
      max_risk: 500,
      legs: [],
    } as PortfolioData["positions"][number],
  ],
  exposure: {},
  violations: [],
};

function CockpitProbe() {
  const { portfolio, setPortfolio } = useTickerDetail();

  useEffect(() => {
    // Simulate WorkspaceShell sync after navigation — arrives after mount.
    const t = setTimeout(() => setPortfolio(SAMPLE), 0);
    return () => clearTimeout(t);
  }, [setPortfolio]);

  if (!portfolio) {
    return <div data-testid="cockpit-empty">no portfolio</div>;
  }
  return (
    <div data-testid="cockpit-ready">
      positions={portfolio.position_count} ticker={portfolio.positions[0]?.ticker}
    </div>
  );
}

describe("TickerDetailContext portfolio reactivity", () => {
  it("re-renders consumers when setPortfolio lands after mount", async () => {
    render(
      <TickerDetailProvider>
        <CockpitProbe />
      </TickerDetailProvider>,
    );

    expect(screen.getByTestId("cockpit-empty")).toBeTruthy();

    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(screen.getByTestId("cockpit-ready").textContent).toContain("positions=1");
    expect(screen.getByTestId("cockpit-ready").textContent).toContain("ticker=GLD");
  });
});
