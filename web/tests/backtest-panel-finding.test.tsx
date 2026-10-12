/**
 * @vitest-environment jsdom
 *
 * Plate 36: a zero-trade backtest leads with the finding, not eight 0.00 tiles.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../components/SignalAreaChart", () => ({ default: () => <div data-testid="backtest-equity-chart" /> }));
vi.mock("../components/BrushMinimap", () => ({ default: () => null }));

import BacktestPanel from "../components/BacktestPanel";

function metrics(n: number) {
  return {
    n_trades: n, sharpe: 1.2, sortino: 1.5, calmar: 0.8, max_drawdown: -0.1,
    hit_rate: 0.55, expectancy: 0.01, annualized_return: 0.12,
    equity_curve: n ? [1, 1.01, 1.02] : [],
  };
}

function stub(body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("BacktestPanel — say the finding", () => {
  it("leads with the zero-trade finding and hides the empty metric grid", async () => {
    const fetchMock = stub({ label: "Crash Risk Index (CRI)", metrics: metrics(0) });
    const { container } = render(<BacktestPanel />);
    const finding = await screen.findByTestId("backtest-finding");
    expect(fetchMock).toHaveBeenCalledWith("/api/backtest/cri", { cache: "no-store" });
    expect(finding.textContent).toBe("0 trades. The entry rule never fired over the available history.");
    expect(container.querySelector(".backtest-metric-grid")).toBeNull();
    expect(screen.queryByText("Sharpe")).toBeNull();
  });

  it("keeps the metric grid when trades exist", async () => {
    stub({ label: "Crash Risk Index (CRI)", metrics: metrics(3), trades: [] });
    const { container } = render(<BacktestPanel />);
    await screen.findByText("Sharpe");
    expect(container.querySelector(".backtest-metric-grid")).toBeTruthy();
    expect(screen.queryByTestId("backtest-finding")).toBeNull();
  });
});
