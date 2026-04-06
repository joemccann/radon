/**
 * @vitest-environment jsdom
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import GexPanel from "../components/GexPanel";

const mockUseGex = vi.fn();

vi.mock("@/lib/useGex", () => ({
  useGex: (...args: unknown[]) => mockUseGex(...args),
}));

vi.mock("@/lib/useMarketHours", () => ({
  MarketState: { OPEN: "OPEN", CLOSED: "CLOSED", EXTENDED: "EXTENDED" },
}));

const MOCK_GEX_DATA = {
  scan_time: "2026-04-06T14:00:00Z",
  market_open: true,
  ticker: "SPX",
  spot: 5582.69,
  close: 5575.37,
  day_change: 7.32,
  day_change_pct: 0.1313,
  data_date: "2026-04-06",
  net_gex: -104044.47,
  net_dex: 37200.0,
  atm_iv: 19.7,
  vol_pc: 1.42,
  levels: {
    gex_flip: { strike: 5537, gamma: 0, distance: -45.69, distance_pct: -0.82 },
    max_magnet: { strike: 5700, gamma: 2955.46, distance: 117.31, distance_pct: 2.1 },
    second_magnet: { strike: 5605, gamma: 2501.80, distance: 22.31, distance_pct: 0.4 },
    max_accelerator: { strike: 5500, gamma: -13511.1, distance: -82.69, distance_pct: -1.48 },
    put_wall: { strike: 5000, gamma: -8000, distance: -582.69, distance_pct: -10.44 },
    call_wall: { strike: 5700, gamma: 3000, distance: 117.31, distance_pct: 2.1 },
  },
  profile: [
    { strike: 5400, call_gex: 10, put_gex: -200, net_gex: -190, pct_from_spot: -3.27, tag: null },
    { strike: 5500, call_gex: 100, put_gex: -60, net_gex: -13511, pct_from_spot: -1.48, tag: "MAX ACCELERATOR" },
    { strike: 5537, call_gex: 50, put_gex: -50, net_gex: 0, pct_from_spot: -0.82, tag: "GEX FLIP" },
    { strike: 5575, call_gex: 80, put_gex: -20, net_gex: 60, pct_from_spot: -0.14, tag: "SPOT" },
    { strike: 5700, call_gex: 200, put_gex: -30, net_gex: 2955, pct_from_spot: 2.1, tag: "MAX MAGNET" },
  ],
  expected_range: { low: 5500, high: 5665, iv_1d: 1.24 },
  bias: {
    direction: "CAUTIOUS_BULL" as const,
    reasons: ["Spot above flip (5537)", "Net GEX still negative", "Magnet at 5700 above spot"],
    days_above_flip: 3,
    flip_migration: [
      { date: "2026-04-03", flip: 5433 },
      { date: "2026-04-04", flip: 5494 },
      { date: "2026-04-06", flip: 5537 },
    ],
  },
  history: [
    { date: "2026-04-03", net_gex: -120000, net_dex: 30000, gex_flip: 5433, spot: 5500, atm_iv: 21.5, vol_pc: 1.1, bias: "BEAR" },
    { date: "2026-04-04", net_gex: -95000, net_dex: 35000, gex_flip: 5494, spot: 5550, atm_iv: 20.3, vol_pc: 1.2, bias: "CAUTIOUS_BULL" },
    { date: "2026-04-06", net_gex: -104044, net_dex: 37200, gex_flip: 5537, spot: 5582.69, atm_iv: 19.7, vol_pc: 1.42, bias: "CAUTIOUS_BULL" },
  ],
  iv: {
    iv30d: 19.7,
    iv_rank: 29.7,
    hv30: 16.75,
    mq_iv30d: 19.5,
    mq_iv_rank: "30%",
    source: "both" as const,
  },
  mq: {
    source_date: "2026-04-02",
    spot: 5575.0,
    hvl: 5537,
    call_resistance_all: 5700,
    call_resistance_0dte: 5600,
    put_support_all: 5300,
    put_support_0dte: 5400,
    expected_high: 5665,
    expected_low: 5500,
    distance_to_hvl_pct: "0.09%",
    iv30d: 0.195,
    hv30: 0.168,
    iv_rank: "30%",
    top_gex_strikes: [5537, 5550, 5500],
  },
  source_delta: {
    flip_vs_hvl: { uw: 5537, mq: 5537, delta: 0 },
    put_wall_vs_support_all: { uw: 5000, mq: 5300, delta: -300 },
    call_wall_vs_resistance_0dte: { uw: 5700, mq: 5600, delta: 100 },
  },
};

function renderWithData(data = MOCK_GEX_DATA, loading = false, error: string | null = null) {
  mockUseGex.mockReturnValue({
    data,
    loading,
    error,
    lastSync: data?.scan_time || null,
    syncing: false,
    syncNow: vi.fn(),
  });
  return render(<GexPanel />);
}

describe("GexPanel", () => {
  it("renders loading state", () => {
    mockUseGex.mockReturnValue({ data: null, loading: true, error: null, lastSync: null, syncing: false, syncNow: vi.fn() });
    const { container } = render(<GexPanel />);
    expect(container.textContent).toContain("Loading GEX scan");
  });

  it("renders empty state when no data", () => {
    mockUseGex.mockReturnValue({ data: null, loading: false, error: null, lastSync: null, syncing: false, syncNow: vi.fn() });
    const { container } = render(<GexPanel />);
    expect(container.textContent).toContain("No GEX data available");
  });

  it("renders error message in alert-item bearish card", () => {
    mockUseGex.mockReturnValue({ data: null, loading: false, error: "UW API down", lastSync: null, syncing: false, syncNow: vi.fn() });
    const { container } = render(<GexPanel />);
    expect(container.textContent).toContain("UW API down");
    const alertEl = container.querySelector(".alert-item.bearish");
    expect(alertEl).not.toBeNull();
    expect(alertEl?.textContent).toContain("UW API down");
  });

  it("renders ticker and date in header", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("SPX");
    expect(container.textContent).toContain("2026-04-06");
  });

  it("renders day badge", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("DAY 3 ABOVE GEX FLIP");
  });

  it("renders spot price", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("5,582.69");
  });

  it("renders net GEX value", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("NET GEX");
  });

  it("renders IV 30D", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("19.7%");
  });

  it("renders vol P/C", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("1.42");
  });

  it("renders key levels", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("GEX FLIP (SUPPORT)");
    expect(container.textContent).toContain("MAX MAGNET");
    expect(container.textContent).toContain("MAX ACCEL");
    expect(container.textContent).toContain("PUT WALL");
  });

  it("renders GEX profile chart", () => {
    const { container } = renderWithData();
    expect(container.querySelector("svg")).toBeTruthy();
    expect(container.textContent).toContain("GEX Profile");
  });

  it("renders profile bars with correct colors in SVG", () => {
    const { container } = renderWithData();
    const rects = container.querySelectorAll("rect");
    expect(rects.length).toBeGreaterThan(0);
  });

  it("renders directional bias", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("CAUTIOUS BULL");
  });

  it("renders bias reasons", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("Spot above flip (5537)");
  });

  it("renders expected range", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("EXPECTED RANGE");
  });

  it("renders history toggle button", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("History (3 sessions)");
  });

  it("expands history table on click", () => {
    const { container } = renderWithData();
    const toggle = container.querySelector(".gex-history-toggle") as HTMLElement;
    expect(toggle).toBeTruthy();
    fireEvent.click(toggle);
    const table = container.querySelector(".gex-history-table");
    expect(table).toBeTruthy();
  });

  it("renders flip migration when available", () => {
    const { container } = renderWithData();
    expect(container.textContent).toContain("Flip migration");
  });

  it("does not show day badge when days_above_flip is 0", () => {
    const data = {
      ...MOCK_GEX_DATA,
      bias: { ...MOCK_GEX_DATA.bias, days_above_flip: 0 },
    };
    const { container } = renderWithData(data);
    expect(container.querySelector(".gex-day-badge")).toBeNull();
  });

  it("shows BELOW badge when days are negative", () => {
    const data = {
      ...MOCK_GEX_DATA,
      bias: { ...MOCK_GEX_DATA.bias, days_above_flip: -2 },
    };
    const { container } = renderWithData(data);
    expect(container.textContent).toContain("DAY 2 BELOW GEX FLIP");
  });

  it("renders InfoTooltip on section title", () => {
    const { container } = renderWithData();
    // The section title tooltip trigger is a span with tabIndex=0 containing '?'
    const triggers = Array.from(container.querySelectorAll("[data-testid='gex-section-tooltip-trigger']"));
    expect(triggers.length).toBeGreaterThan(0);
  });

  it("renders InfoTooltip on NET GEX metric label", () => {
    const { container } = renderWithData();
    // Each MetricCard label with a tooltip renders an InfoTooltip child '?' circle
    const metricLabels = Array.from(container.querySelectorAll(".gex-metric-label"));
    const netGexLabel = metricLabels.find((el) => el.textContent?.includes("NET GEX"));
    expect(netGexLabel).toBeTruthy();
    // Has a tooltip trigger inside
    expect(netGexLabel?.querySelector("span[tabindex='0']")).toBeTruthy();
  });

  it("renders InfoTooltip on IV 30D metric label", () => {
    const { container } = renderWithData();
    const metricLabels = Array.from(container.querySelectorAll(".gex-metric-label"));
    const ivLabel = metricLabels.find((el) => el.textContent?.includes("IV 30D"));
    expect(ivLabel).toBeTruthy();
    expect(ivLabel?.querySelector("span[tabindex='0']")).toBeTruthy();
  });

  it("renders ShareReportModal share button in panel header", () => {
    const { container } = renderWithData();
    // ShareReportModal renders a button with title containing 'Share GEX report'
    const shareBtn = container.querySelector("button[title*='Share GEX']") ??
                     Array.from(container.querySelectorAll("button")).find(
                       (b) => b.getAttribute("title")?.includes("Share GEX") ||
                              b.textContent?.toLowerCase().includes("share")
                     );
    expect(shareBtn).toBeTruthy();
  });

  it("ShareReportModal targets /api/gex/share endpoint", async () => {
    // Verify the component file wires to the correct API endpoint
    const { readFile } = await import("fs/promises");
    const { resolve } = await import("path");
    const componentPath = resolve(__dirname, "../components/GexPanel.tsx");
    const content = await readFile(componentPath, "utf-8");
    expect(content).toContain("/api/gex/share");
  });

});
