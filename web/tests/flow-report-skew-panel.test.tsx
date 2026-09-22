/**
 * @vitest-environment jsdom
 *
 * /flow-analysis/<TICKER> shows the ticker's current 25-delta skew and its
 * direction on both shells. The block comes from the Vol/Skew MR scanner's
 * snapshot inside the flow report payload.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const viewport = vi.hoisted(() => ({ isMobile: false }));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: viewport.isMobile, isTablet: false, hasMounted: true }),
}));

const REPORT = {
  ticker: "META",
  fetched_at: "2026-09-17T14:30:00Z",
  lookback_days: 20,
  verdict: { direction: "BULLISH" as const, confidence: 71 },
  analysis: { direction: "ACCUMULATION", strength: 64 },
  dark_pool: { aggregate: { flow_direction: "ACCUMULATION", flow_strength: 64, dp_buy_ratio: 0.61, num_prints: 900 }, daily: [] },
  options_flow: { bias: "BULLISH", put_call_ratio: 0.7, call_premium: 1_000_000, put_premium: 700_000 },
  skew: {
    expiry: "2026-10-16",
    delta: 25,
    sessions: [
      { date: "2026-09-10", value: 2.4 },
      { date: "2026-09-11", value: 2.6 },
      { date: "2026-09-16", value: 3.1 },
      { date: "2026-09-17", value: 3.42 },
    ],
    value: 3.42,
    prior: 3.1,
    change: 0.32,
    path: "rising" as const,
    errors: [],
  },
  cache_meta: { last_refresh: "2026-09-17T14:30:05Z", age_seconds: 5, is_stale: false },
};

const hookState = vi.hoisted(() => ({
  data: null as Record<string, unknown> | null,
  status: "fresh" as const,
  error: null as string | null,
  refresh: () => {},
}));

vi.mock("@/lib/useTickerFlowReport", () => ({
  useTickerFlowReport: () => hookState,
}));

import TickerFlowReport from "../components/flow-analysis/TickerFlowReport";

afterEach(() => {
  cleanup();
  viewport.isMobile = false;
});

describe("desktop flow report skew panel", () => {
  it("renders the skew value, its direction, the expiry and a sparkline", () => {
    hookState.data = REPORT;
    render(<TickerFlowReport ticker="META" />);

    const panel = screen.getByTestId("flow-skew-panel");
    expect(panel.getAttribute("data-path")).toBe("rising");
    expect(panel.getAttribute("data-tone")).toBe("negative");
    expect(within(panel).getByText("Volatility Skew")).toBeTruthy();
    expect(within(panel).getByTestId("flow-skew-value").textContent).toBe("+3.42");
    expect(within(panel).getByTestId("flow-skew-path").textContent).toBe("RISING");
    expect(within(panel).getByText("+0.32 vs prior session")).toBeTruthy();
    expect(within(panel).getByText(/Oct 16 · \d+ DTE/)).toBeTruthy();
    expect(within(panel).getByTestId("flow-skew-sparkline").querySelectorAll("circle")).toHaveLength(4);
  });

  it("tells the operator to refresh when the cached report predates skew", () => {
    const { skew: _skew, ...withoutSkew } = REPORT;
    hookState.data = withoutSkew;
    render(<TickerFlowReport ticker="META" />);

    const panel = screen.getByTestId("flow-skew-panel");
    expect(within(panel).getByTestId("flow-skew-value").textContent).toBe("--");
    expect(within(panel).getByTestId("flow-skew-path").textContent).toBe("PENDING");
    expect(within(panel).getByText(/refresh/i)).toBeTruthy();
    expect(within(panel).queryByTestId("flow-skew-sparkline")).toBeNull();
  });
});

describe("mobile flow report skew", () => {
  it("shows the skew on the overview and in the options tab", () => {
    viewport.isMobile = true;
    hookState.data = REPORT;
    render(<TickerFlowReport ticker="META" />);

    const overview = screen.getByTestId("flow-skew-panel");
    expect(within(overview).getByTestId("flow-skew-value").textContent).toBe("+3.42");
    expect(within(overview).getByTestId("flow-skew-path").textContent).toBe("RISING");

    fireEvent.click(screen.getByRole("tab", { name: "Options" }));
    const options = screen.getByTestId("flow-skew-panel");
    expect(within(options).getByTestId("flow-skew-value").textContent).toBe("+3.42");
    expect(within(options).getByText(/Oct 16 · \d+ DTE/)).toBeTruthy();
  });
});
