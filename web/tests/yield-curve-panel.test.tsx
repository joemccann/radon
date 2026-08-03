/**
 * @vitest-environment jsdom
 *
 * Yield Curve Indicator — CURVE regime tab (10Y-2Y Treasury spread vs SPX).
 *
 * Pure helpers (lib/yieldCurve.ts): percent formatting, inversion tone
 * thresholds, date display.
 *
 * YieldCurvePanel: gating (SpectralLoader / SectionEmptyState), the header
 * stat strip, the chart title, the default range, and the source footnote.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  formatDateTick,
  formatEtTime,
  formatSessionDate,
  formatSpreadPct,
  formatYieldPct,
  spreadColor,
  type YieldCurveData,
  type YieldCurvePoint,
} from "@/lib/yieldCurve";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatSpreadPct — signed percentage-point display", () => {
  it("signs positive and negative spreads at two decimals", () => {
    expect(formatSpreadPct(0.47)).toBe("+0.47%");
    expect(formatSpreadPct(-0.85)).toBe("-0.85%");
    expect(formatSpreadPct(0)).toBe("+0.00%");
  });

  it("returns --- for null/non-finite", () => {
    expect(formatSpreadPct(null)).toBe("---");
    expect(formatSpreadPct(Number.NaN)).toBe("---");
  });
});

describe("formatYieldPct — unsigned yield display", () => {
  it("formats yields at two decimals", () => {
    expect(formatYieldPct(4.75)).toBe("4.75%");
    expect(formatYieldPct(4.28)).toBe("4.28%");
  });

  it("returns --- for null/non-finite", () => {
    expect(formatYieldPct(null)).toBe("---");
    expect(formatYieldPct(Number.NaN)).toBe("---");
  });
});

describe("spreadColor — inversion tones", () => {
  it("inversion reads negative", () => {
    expect(spreadColor(-0.01)).toBe("var(--negative)");
    expect(spreadColor(-1.08)).toBe("var(--negative)");
  });

  it("flat curve reads as a warning (0 and 0.24 inclusive)", () => {
    expect(spreadColor(0)).toBe("var(--warning)");
    expect(spreadColor(0.24)).toBe("var(--warning)");
  });

  it("a steep curve reads positive (0.25 boundary is positive)", () => {
    expect(spreadColor(0.25)).toBe("var(--positive)");
    expect(spreadColor(0.47)).toBe("var(--positive)");
  });

  it("missing reads muted", () => {
    expect(spreadColor(null)).toBe("var(--text-muted)");
  });
});

describe("date formatting", () => {
  it("formats a live as-of instant as Eastern wall-clock time", () => {
    expect(formatEtTime("2026-08-03T19:35:00Z")).toBe("3:35 PM ET");
    expect(formatEtTime("2026-01-15T19:35:00Z")).toBe("2:35 PM ET");
    expect(formatEtTime(null)).toBe("---");
  });

  it("formats the latest session date compactly", () => {
    expect(formatSessionDate("2026-07-31")).toBe("31 Jul 2026");
    expect(formatSessionDate(null)).toBe("---");
  });

  it("formats daily x-axis ticks as month + year in UTC", () => {
    expect(formatDateTick(new Date("2026-07-31"))).toBe("Jul 2026");
    expect(formatDateTick(new Date("1990-01-02"))).toBe("Jan 1990");
  });
});

/* ─── YieldCurvePanel component ──────────────────────── */

// jsdom doesn't ship ResizeObserver; CriHistoryChart wires one up on mount.
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

const mockUseYieldCurve = vi.fn();
vi.mock("@/lib/useYieldCurve", () => ({
  useYieldCurve: (...args: unknown[]) => mockUseYieldCurve(...args),
}));

const mockUseYieldCurveLive = vi.fn();
vi.mock("@/lib/useYieldCurveLive", () => ({
  useYieldCurveLive: (...args: unknown[]) => mockUseYieldCurveLive(...args),
}));

import YieldCurvePanel from "../components/YieldCurvePanel";
import type { YieldCurveLiveData } from "@/lib/yieldCurve";

function liveHookState(
  partial: Partial<{ data: YieldCurveLiveData | null }> = {},
) {
  return {
    data: null as YieldCurveLiveData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

beforeEach(() => {
  mockUseYieldCurveLive.mockReturnValue(liveHookState());
});

afterEach(() => {
  cleanup();
  mockUseYieldCurve.mockReset();
  mockUseYieldCurveLive.mockReset();
});

function point(overrides: Partial<YieldCurvePoint> = {}): YieldCurvePoint {
  return {
    date: "2026-07-31",
    y3m: 3.83,
    y2: 4.28,
    y10: 4.75,
    spread: 0.47,
    spx_close: 7585.17,
    ...overrides,
  };
}

function buildSeries(count: number): YieldCurvePoint[] {
  return Array.from({ length: count }, (_, i) => {
    const day = new Date(Date.UTC(2020, 0, 1 + i));
    const y2 = 1 + i * 0.01;
    const y10 = 1.6 + i * 0.012;
    return point({
      date: day.toISOString().slice(0, 10),
      y2,
      y10,
      spread: y10 - y2,
      spx_close: 3000 + i * 5,
    });
  });
}

function buildData(overrides: Partial<YieldCurveData> = {}): YieldCurveData {
  const series = buildSeries(40);
  return {
    scan_time: "2026-08-01T22:35:00Z",
    source: "treasury",
    count: series.length,
    current: { date: "2026-07-31", y3m: 3.83, y2: 4.28, y10: 4.75, spread: 0.47 },
    series,
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: YieldCurveData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as YieldCurveData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseYieldCurve.mockReturnValue(state);
  return render(<YieldCurvePanel />);
}

describe("YieldCurvePanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading Treasury yield curve series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the settled missing case", () => {
    renderPanel(
      hookState({
        data: {
          missing: true,
          scan_time: null,
          count: 0,
          series: [],
          current: null,
        },
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No yield curve data yet")).toBeTruthy();
  });
});

describe("YieldCurvePanel — header strip", () => {
  it("renders spread, 10Y, 2Y, and the latest session date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("+0.47%")).toBeTruthy();
    expect(screen.getByText("4.75%")).toBeTruthy();
    expect(screen.getByText("4.28%")).toBeTruthy();
    expect(screen.getByText("31 Jul 2026")).toBeTruthy();
  });

  it("tones the spread by the inversion thresholds", () => {
    renderPanel(hookState({ data: buildData() }));
    const spread = screen.getByTestId("yield-curve-spread-value");
    expect(spread.style.color).toBe("var(--positive)");
  });

  it("tones an inverted curve negative", () => {
    const data = buildData({
      current: { date: "2026-07-31", y3m: 5.4, y2: 4.9, y10: 4.05, spread: -0.85 },
    });
    renderPanel(hookState({ data }));
    const spread = screen.getByTestId("yield-curve-spread-value");
    expect(spread.textContent).toBe("-0.85%");
    expect(spread.style.color).toBe("var(--negative)");
  });
});

describe("YieldCurvePanel — chart + controls", () => {
  it("renders the dual-axis chart title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("S&P 500 VS 10Y-2Y TREASURY SPREAD")).toBeTruthy();
  });

  it("defaults the range to All on the multi-decade daily series", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
  });

  it("renders the source footnote", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(
      screen.getByText(
        "Source: US Treasury daily par yield curve (FRBNY 3:30 PM ET quotes). SPX overlay: Yahoo Finance daily closes.",
      ),
    ).toBeTruthy();
  });

  it("renders the live 10Y-3M estimate cell with a derived as-of time", () => {
    mockUseYieldCurveLive.mockReturnValue(
      liveHookState({
        data: {
          y10: 4.686,
          y3m: 3.7,
          spread_10y_3m: 0.986,
          asof: "2026-08-03T19:35:00Z",
          source: "yahoo",
        },
      }),
    );
    renderPanel(hookState({ data: buildData() }));
    const live = screen.getByTestId("yield-curve-live-value");
    expect(live.textContent).toBe("+0.99%");
    expect(live.style.color).toBe("var(--positive)");
    expect(screen.getByText(/AS OF 3:35 PM ET/)).toBeTruthy();
    expect(screen.getByText("10Y-3M LIVE")).toBeTruthy();
  });

  it("renders --- in the live cell when the estimate is unavailable", () => {
    mockUseYieldCurveLive.mockReturnValue(
      liveHookState({
        data: {
          missing: true,
          y10: null,
          y3m: null,
          spread_10y_3m: null,
          asof: null,
          source: null,
        },
      }),
    );
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("yield-curve-live-value").textContent).toBe("---");
    expect(screen.getByText("ESTIMATE UNAVAILABLE")).toBeTruthy();
  });

  it("does not emit NaN path coordinates when an SPX close is 0 on the log axis", () => {
    const data = buildData();
    data.series[0] = point({ ...data.series[0], spx_close: 0 });
    renderPanel(hookState({ data }));
    const paths = document.querySelectorAll("path");
    for (const path of Array.from(paths)) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});
