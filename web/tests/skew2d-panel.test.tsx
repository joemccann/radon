/**
 * @vitest-environment jsdom
 *
 * SKEW 2D regime tab — two-session change in SPX 1M put/call IV ratio.
 * Pure helpers + panel gating/strip/chart/chips + NaN guard.
 * Spec: docs/indicators/skew2d.md.
 */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  buildSkew2dChartRows,
  formatIvPct,
  formatSkew2dChange,
  formatSkew2dRatio,
  formatZ,
  skew2dChangeColor,
  zScore,
  type Skew2dData,
  type Skew2dEntry,
} from "@/lib/skew2d";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatSkew2dChange / formatSkew2dRatio / formatIvPct / formatZ", () => {
  it("signs the 2d change at two decimals", () => {
    expect(formatSkew2dChange(-0.05)).toBe("-0.05");
    expect(formatSkew2dChange(0.08)).toBe("+0.08");
    expect(formatSkew2dChange(null)).toBe("---");
  });

  it("formats the ratio level unsigned at two decimals", () => {
    expect(formatSkew2dRatio(1.23838134631528)).toBe("1.24");
    expect(formatSkew2dRatio(null)).toBe("---");
  });

  it("formats IV fractions as percentages", () => {
    expect(formatIvPct(0.15)).toBe("15.0%");
    expect(formatIvPct(null)).toBe("---");
  });

  it("formats the z-score signed at one decimal", () => {
    expect(formatZ(-3.0)).toBe("-3.0");
    expect(formatZ(1.25)).toBe("+1.3");
    expect(formatZ(null)).toBe("---");
  });
});

describe("zScore — change over stddev", () => {
  it("divides change by stddev", () => {
    expect(zScore(-0.12, 0.04)).toBeCloseTo(-3.0, 12);
  });

  it("is null-safe on missing inputs and degenerate stddev", () => {
    expect(zScore(null, 0.04)).toBeNull();
    expect(zScore(-0.12, null)).toBeNull();
    expect(zScore(-0.12, 0)).toBeNull();
  });
});

describe("skew2dChangeColor — strict 2-sigma tail warning", () => {
  it("warns strictly beyond two sigma in either direction", () => {
    expect(skew2dChangeColor(-0.12, 0.04)).toBe("var(--warning)");
    expect(skew2dChangeColor(0.081, 0.04)).toBe("var(--warning)");
  });

  it("the boundary and the inside band read muted (strict inequalities)", () => {
    expect(skew2dChangeColor(0.08, 0.04)).toBe("var(--text-muted)");
    expect(skew2dChangeColor(-0.08, 0.04)).toBe("var(--text-muted)");
    expect(skew2dChangeColor(0.01, 0.04)).toBe("var(--text-muted)");
    expect(skew2dChangeColor(null, 0.04)).toBe("var(--text-muted)");
    expect(skew2dChangeColor(0.12, 0)).toBe("var(--text-muted)");
  });
});

describe("buildSkew2dChartRows", () => {
  const series: Skew2dEntry[] = [
    { date: "2026-08-01", ratio: 1.3, change: null },
    { date: "2026-08-04", ratio: 1.28, change: null },
    { date: "2026-08-05", ratio: 1.25, change: -0.05 },
  ];

  it("selects change or level", () => {
    expect(buildSkew2dChartRows(series, "change").map((r) => r.value)).toEqual([
      null, null, -0.05,
    ]);
    expect(buildSkew2dChartRows(series, "level").map((r) => r.value)).toEqual([
      1.3, 1.28, 1.25,
    ]);
  });
});

/* ─── Panel ──────────────────────────────────────────── */

function buildSeries(n: number): Skew2dEntry[] {
  const out: Skew2dEntry[] = [];
  const day = new Date(Date.UTC(2024, 0, 2));
  for (let i = 0; i < n; i++) {
    while (day.getUTCDay() === 0 || day.getUTCDay() === 6) {
      day.setUTCDate(day.getUTCDate() + 1);
    }
    out.push({
      date: day.toISOString().slice(0, 10),
      ratio: 1.25 + (i % 5) * 0.01,
      change: i < 2 ? null : ((i % 7) - 3) * 0.01,
    });
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return out;
}

function hookState(overrides: Partial<{
  data: Skew2dData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
}> = {}) {
  return {
    data: null as Skew2dData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    triggerSync: vi.fn(),
    ...overrides,
  };
}

const useSkew2dMock = vi.fn();
vi.mock("@/lib/useSkew2d", () => ({
  useSkew2d: (...args: unknown[]) => useSkew2dMock(...args),
}));

vi.mock("@/lib/useMarketHours", () => ({
  useMarketHours: () => ({ state: "open", label: "OPEN" }),
  MarketState: { OPEN: "open", CLOSED: "closed", PREMARKET: "premarket", AFTERHOURS: "afterhours" },
}));

beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

afterEach(() => {
  cleanup();
  useSkew2dMock.mockReset();
});

describe("Skew2dPanel", () => {
  it("shows loader while first fetch is in flight", async () => {
    useSkew2dMock.mockReturnValue(hookState({ loading: true, data: null }));
    const { Skew2dPanel } = await import("@/components/Skew2dPanel");
    render(<Skew2dPanel />);
    expect(screen.getByText(/Loading 2d skew series/i)).toBeTruthy();
  });

  it("shows empty state on missing:true", async () => {
    useSkew2dMock.mockReturnValue(
      hookState({
        data: {
          missing: true,
          scan_time: null,
          count: 0,
          series: [],
          current: null,
          stats: null,
        },
      }),
    );
    const { Skew2dPanel } = await import("@/components/Skew2dPanel");
    render(<Skew2dPanel />);
    expect(screen.getByText(/No 2d skew data yet/i)).toBeTruthy();
  });

  it("renders strip values, chart title, and view chips", async () => {
    const series = buildSeries(40);
    const data: Skew2dData = {
      scan_time: "2026-08-09T21:50:00Z",
      source: "skew_history",
      count: series.length,
      current: {
        date: series[series.length - 1].date,
        ratio: 1.24,
        change: 0.08,
        put_iv: 0.15,
        call_iv: 0.12,
        expiry: "2026-08-21",
        dte: 14,
      },
      stats: { high: 0.27, low: -0.21, avg: 0, stddev: 0.03 },
      series,
    };
    useSkew2dMock.mockReturnValue(hookState({ data, lastSync: data.scan_time }));
    const { Skew2dPanel } = await import("@/components/Skew2dPanel");
    const { container } = render(<Skew2dPanel />);

    expect(screen.getByTestId("skew2d-change-value").textContent).toMatch(/\+0\.08/);
    expect(screen.getByText(/2D CHANGE IN 1M SPX PUT\/CALL SKEW/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /CHANGE/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /LEVEL/i })).toBeTruthy();

    // NaN guard: no chart path may contain the string NaN
    const paths = Array.from(container.querySelectorAll("path"));
    for (const p of paths) {
      const d = p.getAttribute("d") ?? "";
      expect(d).not.toMatch(/NaN/i);
    }
  });

  it("toggles LEVEL view without introducing NaN paths", async () => {
    const series = buildSeries(40);
    const data: Skew2dData = {
      scan_time: "2026-08-09T21:50:00Z",
      count: series.length,
      current: {
        date: series[series.length - 1].date,
        ratio: 1.24,
        change: 0.08,
        put_iv: 0.15,
        call_iv: 0.12,
        expiry: "2026-08-21",
        dte: 14,
      },
      stats: { high: 0.27, low: -0.21, avg: 0, stddev: 0.03 },
      series,
    };
    useSkew2dMock.mockReturnValue(hookState({ data, lastSync: data.scan_time }));
    const { Skew2dPanel } = await import("@/components/Skew2dPanel");
    const { container } = render(<Skew2dPanel />);
    fireEvent.click(screen.getByRole("button", { name: /^LEVEL$/i }));
    for (const p of Array.from(container.querySelectorAll("path"))) {
      expect(p.getAttribute("d") ?? "").not.toMatch(/NaN/i);
    }
  });
});
