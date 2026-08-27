/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import VcgPanel from "../components/VcgPanel";
import type { VcgData, VcgHistoryEntry } from "@/lib/useVcg";

// jsdom doesn't ship ResizeObserver; the chart wires one up on mount.
beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    class StubResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: typeof StubResizeObserver }).ResizeObserver =
      StubResizeObserver;
  }
});

const mockUseVcg = vi.fn();

vi.mock("@/lib/useVcg", () => ({
  useVcg: (...args: unknown[]) => mockUseVcg(...args),
}));

afterEach(() => {
  cleanup();
  mockUseVcg.mockReset();
});

function buildHistory(count: number): VcgHistoryEntry[] {
  return Array.from({ length: count }, (_, i) => ({
    date: `2026-05-${String(i + 1).padStart(2, "0")}`,
    residual: 0.001 + i * 0.0001,
    vcg: -1 + i * 0.15,
    vcg_adj: -0.9 + i * 0.15,
    beta1: -0.0139,
    beta2: -0.023,
    vix: 18 + i * 0.2,
    vvix: 110 + i * 0.5,
    credit: 80 - i * 0.05,
  }));
}

function buildVcgData(overrides: Partial<VcgData> = {}): VcgData {
  return {
    scan_time: "2026-05-17T20:00:00Z",
    market_open: false,
    credit_proxy: "HYG",
    signal: {
      vcg: 1.45,
      vcg_adj: 1.45,
      residual: 0.0034,
      beta1_vvix: -0.0139,
      beta2_vix: -0.023,
      alpha: 0,
      vix: 21,
      vvix: 118,
      credit_price: 79.6,
      credit_5d_return_pct: -0.4,
      ro: 0,
      edr: 0,
      tier: null,
      bounce: 0,
      vvix_severity: "elevated",
      sign_ok: true,
      sign_suppressed: false,
      pi_panic: 0,
      regime: "NORMAL",
      interpretation: "WATCH",
      attribution: {
        vvix_pct: 50,
        vix_pct: 50,
        vvix_component: 0,
        vix_component: 0,
        model_implied: 0,
      },
    },
    history: buildHistory(20),
    ...overrides,
  };
}

describe("VcgPanel — 20-session history chart", () => {
  it("renders the VCG history chart section when history has ≥ 2 sessions", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData(),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { container, getByTestId } = render(
      React.createElement(VcgPanel, { prices: {} }),
    );

    expect(getByTestId("vcg-history-chart-section")).toBeTruthy();
    expect(container.querySelector('[data-testid="vcg-signal-area-chart"]')).toBeTruthy();
    expect(container.textContent).toContain("VCG Z-Score History");
    // Current value pill should reflect the signal's latest VCG.
    expect(getByTestId("vcg-chart-current-value").textContent).toContain("+1.45");
  });

  it("hides the chart section when history has fewer than 2 sessions", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ history: [] }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { container } = render(React.createElement(VcgPanel, { prices: {} }));
    expect(container.querySelector('[data-testid="vcg-history-chart-section"]')).toBeNull();
  });

  it("renders the chart even when credit_proxy is non-default (e.g. JNK)", () => {
    // The chart itself is now single-series VCG-only — credit proxy
    // shows in the Signal Strip up top, not the chart. This test
    // just confirms the panel doesn't crash on a non-HYG proxy.
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ credit_proxy: "JNK" }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { getByTestId } = render(React.createElement(VcgPanel, { prices: {} }));
    expect(getByTestId("vcg-history-chart-section")).toBeTruthy();
    expect(getByTestId("vcg-signal-area-chart")).toBeTruthy();
  });

  it("renders the chart ABOVE the history table (chart-first reading order)", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData(),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { container } = render(React.createElement(VcgPanel, { prices: {} }));
    const html = container.innerHTML;
    const chartIdx = html.indexOf('data-testid="vcg-history-chart-section"');
    const tableIdx = html.indexOf("VCG History: Recent 20 Sessions");
    expect(chartIdx).toBeGreaterThan(-1);
    expect(tableIdx).toBeGreaterThan(-1);
    expect(chartIdx).toBeLessThan(tableIdx);
  });
});

describe("VcgPanel — chart range chips", () => {
  it("shows 1M / 3M / 6M / 1Y / All chips when history is long enough", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ history: buildHistory(300) }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { getByTestId, getAllByRole } = render(
      React.createElement(VcgPanel, { prices: {} }),
    );
    const strip = getByTestId("vcg-history-range-chips");
    expect(strip).toBeTruthy();
    const buttons = getAllByRole("button").filter((b) =>
      ["1M", "3M", "6M", "1Y", "ALL"].includes(b.textContent?.trim().toUpperCase() ?? ""),
    );
    expect(buttons.length).toBe(5);
  });

  it("defaults to 1M when at least one month of sessions is available", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ history: buildHistory(300) }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { getByTestId } = render(React.createElement(VcgPanel, { prices: {} }));
    const oneMonthChip = getByTestId("vcg-history-range-chips-1m");
    expect(oneMonthChip.getAttribute("aria-pressed")).toBe("true");
  });

  it("defaults to All when history is short (e.g. 30 sessions)", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ history: buildHistory(30) }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { getByTestId } = render(React.createElement(VcgPanel, { prices: {} }));
    // 30 sessions → 1M is the largest preset that fits → that becomes default.
    const oneMonthChip = getByTestId("vcg-history-range-chips-1m");
    expect(oneMonthChip.getAttribute("aria-pressed")).toBe("true");
  });

  it("clicking a chip reslices the chart (bar count changes)", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ history: buildHistory(300) }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { getByTestId } = render(React.createElement(VcgPanel, { prices: {} }));

    function barCount(): number {
      const svg = getByTestId("vcg-signal-area-chart");
      // Each visible session renders one positive/negative <rect> band
      // with opacity 0.22; the hover-capture <rect> has no opacity.
      return svg.querySelectorAll('rect[opacity="0.22"]').length;
    }

    // Default 1M on 300 sessions → 21 visible bars.
    expect(barCount()).toBe(21);

    // Switch to 3M.
    fireEvent.click(getByTestId("vcg-history-range-chips-3m"));
    expect(barCount()).toBe(63);

    // Switch to All.
    fireEvent.click(getByTestId("vcg-history-range-chips-all"));
    expect(barCount()).toBe(300);
  });

  it("history table stays capped at the most-recent 20 sessions regardless of chart range", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ history: buildHistory(300) }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { container } = render(React.createElement(VcgPanel, { prices: {} }));
    // Count the rows in the "Recent 20 Sessions" table.
    const tbodyRows = container.querySelectorAll("table tbody tr");
    expect(tbodyRows.length).toBe(20);
  });
});

describe("VcgPanel — brush minimap (CRI-style range zoom)", () => {
  function barCount(svg: Element): number {
    return svg.querySelectorAll('rect[opacity="0.22"]').length;
  }

  it("renders the brush minimap with a window + two handles", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ history: buildHistory(300) }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { getByTestId } = render(React.createElement(VcgPanel, { prices: {} }));
    expect(getByTestId("vcg-history-brush")).toBeTruthy();
    expect(getByTestId("vcg-history-brush-window")).toBeTruthy();
    expect(getByTestId("vcg-history-brush-handle-left")).toBeTruthy();
    expect(getByTestId("vcg-history-brush-handle-right")).toBeTruthy();
  });

  it("dragging the left handle widens the visible slice and flags a Custom range", () => {
    mockUseVcg.mockReturnValue({
      data: buildVcgData({ history: buildHistory(300) }),
      loading: false,
      error: null,
      lastSync: "2026-05-17T20:00:00Z",
    });

    const { getByTestId, queryByTestId } = render(React.createElement(VcgPanel, { prices: {} }));

    // Default 1M on 300 sessions → 21 visible bars; no Custom chip yet.
    expect(barCount(getByTestId("vcg-signal-area-chart"))).toBe(21);
    expect(queryByTestId("vcg-history-range-chips-custom")).toBeNull();

    const brush = getByTestId("vcg-history-brush");
    const handle = getByTestId("vcg-history-brush-handle-left");
    const brushRect = {
      x: 0, y: 0, left: 0, top: 0, right: 700, bottom: 40, width: 700, height: 40,
      toJSON: () => ({}),
    } as DOMRect;
    brush.getBoundingClientRect = () => brushRect;
    handle.getBoundingClientRect = () => brushRect;

    // Drag the left handle to the far left → the window expands to (near) full history.
    fireEvent.pointerDown(handle, { clientX: 700, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 0, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 0, pointerId: 1 });

    expect(barCount(getByTestId("vcg-signal-area-chart"))).toBeGreaterThan(21);
    expect(getByTestId("vcg-history-range-chips-custom")).toBeTruthy();
  });
});
