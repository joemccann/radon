/**
 * @vitest-environment jsdom
 *
 * SKEW regime tab — change in the SPX 1M 25-delta put/call IV ratio.
 *
 * Pure helpers (lib/skew.ts): signed change / ratio / IV / z-score formatting,
 * the strict 2-sigma warning tone, chart-row selection for the CHANGE and
 * LEVEL views. SkewPanel: gating, strip, chart title, view chips, NaN guard.
 * Spec: docs/indicators/skew.md.
 */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  buildSkewChartRows,
  formatIvPct,
  formatSkewChange,
  formatSkewRatio,
  formatZ,
  skewChangeColor,
  zScore,
  type SkewData,
  type SkewEntry,
} from "@/lib/skew";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatSkewChange / formatSkewRatio / formatIvPct / formatZ", () => {
  it("signs the daily change at two decimals", () => {
    expect(formatSkewChange(-0.12)).toBe("-0.12");
    expect(formatSkewChange(0.05)).toBe("+0.05");
    expect(formatSkewChange(null)).toBe("---");
  });

  it("formats the ratio level unsigned at two decimals", () => {
    expect(formatSkewRatio(1.2929992556526146)).toBe("1.29");
    expect(formatSkewRatio(null)).toBe("---");
  });

  it("formats IV fractions as percentages", () => {
    expect(formatIvPct(0.15956701505157658)).toBe("16.0%");
    expect(formatIvPct(0.12340843535214445)).toBe("12.3%");
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

describe("skewChangeColor — strict 2-sigma tail warning", () => {
  it("warns strictly beyond two sigma in either direction", () => {
    expect(skewChangeColor(-0.12, 0.04)).toBe("var(--warning)");
    expect(skewChangeColor(0.081, 0.04)).toBe("var(--warning)");
  });

  it("the boundary and the inside band read muted (strict inequalities)", () => {
    expect(skewChangeColor(0.08, 0.04)).toBe("var(--text-muted)");
    expect(skewChangeColor(-0.08, 0.04)).toBe("var(--text-muted)");
    expect(skewChangeColor(0.01, 0.04)).toBe("var(--text-muted)");
    expect(skewChangeColor(null, 0.04)).toBe("var(--text-muted)");
    expect(skewChangeColor(-0.12, null)).toBe("var(--text-muted)");
  });
});

function entry(overrides: Partial<SkewEntry> = {}): SkewEntry {
  return {
    date: "2026-08-05",
    ratio: 1.292999,
    change: -0.12,
    ...overrides,
  };
}

describe("buildSkewChartRows — view selection with nulls preserved", () => {
  const series = [
    entry({ date: "2026-08-01", ratio: 1.4, change: null }),
    entry({ date: "2026-08-04", ratio: 1.413, change: 0.013 }),
    entry(),
  ];

  it("change view plots the daily change, keeping the null first row", () => {
    const rows = buildSkewChartRows(series, "change");
    expect(rows.map((r) => r.date)).toEqual(["2026-08-01", "2026-08-04", "2026-08-05"]);
    expect(rows.map((r) => r.value)).toEqual([null, 0.013, -0.12]);
  });

  it("level view plots the ratio", () => {
    const rows = buildSkewChartRows(series, "level");
    expect(rows.map((r) => r.value)).toEqual([1.4, 1.413, 1.292999]);
  });
});

/* ─── SkewPanel component ────────────────────────────── */

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

const mockUseSkew = vi.fn();
vi.mock("@/lib/useSkew", () => ({
  useSkew: (...args: unknown[]) => mockUseSkew(...args),
}));

import SkewPanel from "../components/SkewPanel";

afterEach(() => {
  cleanup();
  mockUseSkew.mockReset();
});

function buildSeries(count: number): SkewEntry[] {
  return Array.from({ length: count }, (_, i) => {
    const day = new Date(Date.UTC(2026, 0, 5 + i));
    return entry({
      date: day.toISOString().slice(0, 10),
      ratio: 1.3 + (i % 9) * 0.01,
      change: i === 0 ? null : ((i % 7) - 3) * 0.01,
    });
  });
}

function buildData(overrides: Partial<SkewData> = {}): SkewData {
  const series = buildSeries(120);
  return {
    scan_time: "2026-08-05T21:45:00Z",
    source: "unusual_whales",
    count: series.length,
    current: {
      date: "2026-08-05",
      ratio: 1.292999,
      change: -0.12,
      put_iv: 0.159567,
      call_iv: 0.123408,
      expiry: "2026-09-18",
      dte: 44,
    },
    stats: { high: 0.13, low: -0.16, avg: 0.0004, stddev: 0.04 },
    series,
    ...overrides,
  };
}

function hookState(partial: Partial<{
  data: SkewData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
}> = {}) {
  return {
    data: null as SkewData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseSkew.mockReturnValue(state);
  return render(<SkewPanel />);
}

describe("SkewPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading UW skew series")).toBeTruthy();
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
          stats: null,
        },
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No skew data yet")).toBeTruthy();
  });
});

describe("SkewPanel — header strip", () => {
  it("renders change, level, z-score, both wing IVs, and the tenor", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("-0.12")).toBeTruthy(); // change
    expect(screen.getByText("1.29")).toBeTruthy(); // ratio level
    expect(screen.getByText("-3.0")).toBeTruthy(); // z-score
    expect(screen.getByText("16.0%")).toBeTruthy(); // 25d put IV
    expect(screen.getByText("12.3%")).toBeTruthy(); // 25d call IV
    expect(screen.getByText("2026-08-05")).toBeTruthy(); // latest date
  });

  it("tones a beyond-2-sigma change as a warning", () => {
    renderPanel(hookState({ data: buildData() }));
    const change = screen.getByTestId("skew-change-value");
    expect(change.style.color).toBe("var(--warning)");
  });
});

describe("SkewPanel — chart + views", () => {
  it("renders the chart with the CHANGE view by default", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("SPX 1M 25D PUT/CALL SKEW")).toBeTruthy();
    const changeChip = screen.getByRole("button", { name: "CHANGE" });
    expect(changeChip.getAttribute("aria-pressed")).toBe("true");
  });

  it("switches to the LEVEL view when its chip is clicked", () => {
    renderPanel(hookState({ data: buildData() }));
    const levelChip = screen.getByRole("button", { name: "LEVEL" });
    fireEvent.click(levelChip);
    expect(levelChip.getAttribute("aria-pressed")).toBe("true");
  });

  it("does not emit NaN path coordinates with the null first change present", () => {
    renderPanel(hookState({ data: buildData() }));
    const paths = document.querySelectorAll("path");
    for (const path of Array.from(paths)) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});
