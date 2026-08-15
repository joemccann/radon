/**
 * @vitest-environment jsdom
 *
 * Production /performance crash: Turso serves the TWR builder snapshot, which
 * has no contracts_missing_history / trades_source / price_sources. The panel
 * then throws TypeError: Cannot read properties of undefined (reading 'length').
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockUsePerformance = vi.fn();
vi.mock("@/lib/usePerformance", () => ({
  usePerformance: (...args: unknown[]) => mockUsePerformance(...args),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({
    viewportClass: "desktop",
    isMobile: false,
    isTablet: false,
    isDesktop: true,
    width: 1280,
    hasMounted: true,
  }),
}));

import PerformancePanel from "../components/PerformancePanel";
import MobilePerformancePanel from "../components/mobile/MobilePerformancePanel";

afterEach(() => {
  cleanup();
  mockUsePerformance.mockReset();
});

/** Shape persisted by perf_twr_builder.py and served from performance_snapshots. */
function buildTwrSnapshot() {
  return {
    status: "ok",
    methodology: {
      curve_type: "twr_modified_dietz_daily",
      return_basis: "time_weighted",
      risk_free_rate: 0.0412,
      library_strategy: "fred_dgs3mo",
    },
    summary: {
      total_return: 0.1,
      annualized_return: 0.5,
      trading_days: 57,
      period_start: "2026-01-02",
      period_end: "2026-03-20",
      max_drawdown: -0.05,
      sharpe_ratio: 1.2,
      beta: 0.8,
      alpha: 0.02,
      var_95: null,
      cvar_95: null,
    },
    metrics: { tracking_error: 0.12 },
    series: [
      { date: "2026-01-02", nav: 100_000, return: 0, cum_return: 0, equity: 100 },
      { date: "2026-01-05", nav: 105_000, return: 0.05, cum_return: 0.05, equity: 105 },
      { date: "2026-03-20", nav: 110_000, return: 0.047619, cum_return: 0.1, equity: 110 },
    ],
    warnings: [],
    period_start: "2026-01-02",
    period_end: "2026-03-20",
    benchmark: "SPY",
    nav_source: "disk_cache",
  };
}

function stubPerformance(data: unknown) {
  mockUsePerformance.mockReturnValue({
    data,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  });
}

describe("PerformancePanel TWR snapshot", () => {
  it("renders the desktop panel from a persisted TWR payload that omits reconstruction arrays", () => {
    stubPerformance(buildTwrSnapshot());

    expect(() => render(<PerformancePanel />)).not.toThrow();

    expect(screen.getByTestId("performance-panel")).toBeTruthy();
    expect(screen.getByTestId("performance-hero-twr").textContent).toBe("+10.00%");
    expect(screen.getByTestId("performance-hero-subtitle").textContent).toContain("Ending equity $110,000.00");
    expect(screen.getByTestId("performance-source-pill").textContent).toMatch(/IB NAV/i);
  });

  it("renders the insufficient-data empty state when warnings and series are absent", () => {
    stubPerformance({
      status: "insufficient_data",
      methodology: {
        curve_type: "twr_modified_dietz_daily",
        return_basis: "time_weighted",
        risk_free_rate: 0,
        library_strategy: "fred_dgs3mo",
      },
      summary: {},
    });

    expect(() => render(<PerformancePanel />)).not.toThrow();
    expect(screen.getByTestId("performance-insufficient")).toBeTruthy();
  });
});

describe("MobilePerformancePanel TWR snapshot", () => {
  it("renders from the same incomplete TWR payload", () => {
    stubPerformance(buildTwrSnapshot());

    expect(() => render(<MobilePerformancePanel />)).not.toThrow();

    expect(screen.getByTestId("performance-panel")).toBeTruthy();
    expect(screen.getByTestId("performance-hero-twr").textContent).toBe("+10.00%");
    expect(screen.getByTestId("performance-hero-subtitle").textContent).toContain("Ending equity $110,000.00");
  });
});
