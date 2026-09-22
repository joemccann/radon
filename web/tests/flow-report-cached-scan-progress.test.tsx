/**
 * @vitest-environment jsdom
 *
 * Clicking Analyze on /flow-analysis/META paints the last cached report while
 * POST is still in flight (`status === "scanning"`). That state was not
 * treated as cached, so the hero matched a finished scan and the only
 * activity mark was a static refresh glyph labeled ANALYZING.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, hasMounted: true }),
}));

const hookState = vi.hoisted(() => ({
  data: null as unknown,
  status: "fresh" as string,
  error: null as string | null,
  refresh: () => {},
}));

vi.mock("@/lib/useTickerFlowReport", () => ({
  useTickerFlowReport: () => hookState,
}));

import TickerFlowReport from "../components/flow-analysis/TickerFlowReport";

const META = {
  ticker: "META",
  fetched_at: "2026-09-21T19:50:06Z",
  lookback_days: 20,
  verdict: { direction: "BULLISH" as const, confidence: 28 },
  dark_pool: {
    aggregate: {
      flow_direction: "ACCUMULATION",
      flow_strength: 13.1,
      dp_buy_ratio: 0.57,
      num_prints: 324608,
      total_volume: 213_210_000,
      total_premium: 130_970_000,
    },
  },
  options_flow: {
    bias: "STRONGLY_BULLISH",
    put_call_ratio: 0.36,
    call_premium: 1_280_000,
    put_premium: 464_420_000,
  },
  market_status: "MARKET OPEN (3.6H ELAPSED, 55% OF DAY)",
};

function renderWith(state: Record<string, unknown>) {
  Object.assign(hookState, state);
  return render(<TickerFlowReport ticker="META" />);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  Object.assign(hookState, { data: null, status: "fresh", error: null, refresh: () => {} });
});

describe("TickerFlowReport — cached figures while a scan is in flight", () => {
  function freeze() {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-21T20:00:00Z"));
  }

  it("marks the on-screen report as cached and shows a live scan indicator", () => {
    freeze();
    const { container } = renderWith({ data: META, status: "scanning", error: null });

    const badge = container.querySelector(".ticker-flow-badge");
    expect(badge?.getAttribute("data-status")).toBe("scanning");
    expect(badge?.getAttribute("data-stale")).toBe("true");
    expect(screen.getByTestId("flow-hero-stale").textContent).toContain("LAST GOOD SCAN");
    expect(screen.getByTestId("flow-hero-stale").textContent).toContain("2026-09-21");
    expect(screen.queryByTestId("flow-stale-age")).toBeNull();

    const progress = screen.getByTestId("flow-scan-progress");
    expect(progress.textContent).toMatch(/scan running/i);
    expect(progress.textContent).toMatch(/cached report/i);
    expect(progress.textContent).toContain("2026-09-21");
    expect(progress.textContent).toMatch(/figures update when the scan lands/i);
    expect(progress.querySelector(".spectral-loader")).not.toBeNull();

    const refresh = screen.getByLabelText("Refresh flow report");
    expect(refresh.getAttribute("aria-busy")).toBe("true");
    expect(refresh.querySelector("[data-testid='thinking-wait']")).not.toBeNull();
    expect(refresh.querySelector("svg")).toBeNull();
    expect(container.querySelector(".ticker-flow-analyzing-spinner")).toBeNull();

    expect(screen.getByText("Bullish")).toBeTruthy();
    expect(screen.getByText("28")).toBeTruthy();
    expect(screen.getByText("324,608")).toBeTruthy();
  });

  it("keeps the same live indicator while the server scan is still pending", () => {
    freeze();
    renderWith({ data: META, status: "pending", error: null });

    expect(screen.getByTestId("flow-scan-progress").textContent).toMatch(/cached report/i);
    expect(screen.getByTestId("flow-hero-pending").textContent).toMatch(/still running/i);
    expect(screen.getByLabelText("Refresh flow report").getAttribute("aria-busy")).toBe("true");
  });

  it("drops the cached-scan treatment once the report is fresh", () => {
    freeze();
    renderWith({ data: META, status: "fresh", error: null });

    expect(screen.queryByTestId("flow-scan-progress")).toBeNull();
    expect(screen.queryByTestId("flow-hero-stale")).toBeNull();
    expect(screen.queryByTestId("flow-stale-age")).toBeNull();
    const refresh = screen.getByLabelText("Refresh flow report");
    expect(refresh.getAttribute("aria-busy")).not.toBe("true");
    expect(refresh.querySelector("[data-testid='thinking-wait']")).toBeNull();
    expect(refresh.querySelector("svg")).not.toBeNull();
  });
});
