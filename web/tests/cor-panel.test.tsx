/**
 * @vitest-environment jsdom
 *
 * COR regime tab — SPX implied correlation (Cboe COR1M/3M/6M/1Y).
 *
 * Pure helpers (lib/cor.ts): value/change/percentile formatting, the strict
 * regime thresholds, term spread, newest-source-stamp selection, chart-row
 * selection with nulls preserved.
 *
 * CorPanel: gating (SpectralLoader / SectionEmptyState), the header strip,
 * the chart title, tenor chips, and the NaN path guard.
 * Spec: docs/indicators/cor.md.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  buildCorChartRows,
  corRegime,
  corRegimeColor,
  formatCor,
  formatCorChange,
  formatPercentile,
  latestSourceStamp,
  termSpread,
  TENOR_OPTIONS,
  type CorData,
  type CorEntry,
} from "@/lib/cor";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatCor — two-decimal display", () => {
  it("formats index points", () => {
    expect(formatCor(12.16)).toBe("12.16");
    expect(formatCor(7.38)).toBe("7.38");
    expect(formatCor(96.59)).toBe("96.59");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatCor(null)).toBe("---");
    expect(formatCor(undefined)).toBe("---");
    expect(formatCor(Number.NaN)).toBe("---");
  });
});

describe("formatCorChange — signed two-decimal display", () => {
  it("signs positive and negative changes", () => {
    expect(formatCorChange(0.98)).toBe("+0.98");
    expect(formatCorChange(-1.07)).toBe("-1.07");
  });

  it("returns --- for null", () => {
    expect(formatCorChange(null)).toBe("---");
  });
});

describe("formatPercentile — fraction to one-decimal percent", () => {
  it("formats the all-history percentile", () => {
    expect(formatPercentile(0.0083)).toBe("0.8%");
    expect(formatPercentile(0.9312)).toBe("93.1%");
  });

  it("returns --- for null", () => {
    expect(formatPercentile(null)).toBe("---");
  });
});

describe("corRegime — strict percentile thresholds", () => {
  it("below the 10th percentile reads COMPRESSED", () => {
    expect(corRegime(0.0083)).toBe("COMPRESSED");
    expect(corRegime(0.0999)).toBe("COMPRESSED");
  });

  it("above the 90th percentile reads STRESS", () => {
    expect(corRegime(0.9001)).toBe("STRESS");
    expect(corRegime(1)).toBe("STRESS");
  });

  it("the boundaries and the middle band read NEUTRAL (strict inequalities)", () => {
    expect(corRegime(0.1)).toBe("NEUTRAL");
    expect(corRegime(0.9)).toBe("NEUTRAL");
    expect(corRegime(0.5)).toBe("NEUTRAL");
    expect(corRegime(null)).toBe("NEUTRAL");
  });
});

describe("corRegimeColor — brand tones", () => {
  it("maps each regime to its token", () => {
    expect(corRegimeColor("COMPRESSED")).toBe("var(--warning)");
    expect(corRegimeColor("STRESS")).toBe("var(--negative)");
    expect(corRegimeColor("NEUTRAL")).toBe("var(--text-muted)");
  });
});

function entry(overrides: Partial<CorEntry> = {}): CorEntry {
  return {
    date: "2026-08-07",
    cor1m: 7.38,
    cor3m: 10.48,
    cor6m: 12.16,
    cor1y: 14.19,
    ...overrides,
  };
}

describe("termSpread — 1Y minus 1M, null-safe", () => {
  it("computes the spread from the current card", () => {
    expect(termSpread({ cor1m: 7.38, cor1y: 14.19 })).toBeCloseTo(6.81, 10);
  });

  it("returns null when either leg is null", () => {
    expect(termSpread({ cor1m: null, cor1y: 14.19 })).toBeNull();
    expect(termSpread({ cor1m: 7.38, cor1y: null })).toBeNull();
    expect(termSpread(null)).toBeNull();
  });
});

const STAMPS = {
  cor1m: "Fri, 07 Aug 2026 22:31:04 GMT",
  cor3m: "Fri, 07 Aug 2026 22:31:10 GMT",
  cor6m: "Fri, 07 Aug 2026 22:31:16 GMT",
  cor1y: "Fri, 07 Aug 2026 22:31:22 GMT",
};

describe("latestSourceStamp — newest of the four files", () => {
  it("returns the newest parseable stamp", () => {
    expect(latestSourceStamp(STAMPS)).toBe("Fri, 07 Aug 2026 22:31:22 GMT");
  });

  it("ignores unparseable stamps and returns null when none parse", () => {
    expect(latestSourceStamp({ ...STAMPS, cor1y: "not-a-date" })).toBe(
      "Fri, 07 Aug 2026 22:31:16 GMT",
    );
    expect(latestSourceStamp({ cor1m: "nope" })).toBeNull();
    expect(latestSourceStamp(null)).toBeNull();
  });
});

describe("buildCorChartRows — tenor selection with nulls preserved", () => {
  it("maps the selected tenor and keeps gaps as nulls", () => {
    const rows = buildCorChartRows(
      [
        entry({ date: "2006-05-17", cor3m: 31.1 }),
        entry({ date: "2006-05-18", cor3m: null }),
        entry({ date: "2006-05-19", cor3m: 30.9 }),
      ],
      "cor3m",
    );
    expect(rows.map((r) => r.date)).toEqual(["2006-05-17", "2006-05-18", "2006-05-19"]);
    expect(rows.map((r) => r.value)).toEqual([31.1, null, 30.9]);
  });
});

describe("TENOR_OPTIONS — chip order", () => {
  it("offers the four tenors in ascending order", () => {
    expect(TENOR_OPTIONS.map((o) => o.key)).toEqual(["cor1m", "cor3m", "cor6m", "cor1y"]);
    expect(TENOR_OPTIONS.map((o) => o.label)).toEqual(["1M", "3M", "6M", "1Y"]);
  });
});

/* ─── CorPanel component ─────────────────────────────── */

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

const mockUseCor = vi.fn();
vi.mock("@/lib/useCor", () => ({
  useCor: (...args: unknown[]) => mockUseCor(...args),
}));

import CorPanel from "../components/CorPanel";

afterEach(() => {
  cleanup();
  mockUseCor.mockReset();
});

function buildSeries(count: number): CorEntry[] {
  return Array.from({ length: count }, (_, i) => {
    const day = new Date(Date.UTC(2026, 0, 5 + i));
    return entry({
      date: day.toISOString().slice(0, 10),
      cor1m: 20 + (i % 9),
      cor3m: i % 11 === 0 ? null : 25 + (i % 7),
      cor6m: 30 + (i % 5),
      cor1y: 35 + (i % 3),
    });
  });
}

function buildData(overrides: Partial<CorData> = {}): CorData {
  const series = [...buildSeries(119), entry()];
  return {
    scan_time: "2026-08-10T02:20:00Z",
    source_last_modified: STAMPS,
    count: series.length,
    current: {
      date: "2026-08-07",
      cor1m: 7.38,
      cor3m: 10.48,
      cor6m: 12.16,
      cor1y: 14.19,
      term_spread: 6.81,
      change_1d: { cor1m: 0.76, cor3m: 1.07, cor6m: 0.98, cor1y: 0.74 },
    },
    stats: {
      cor1m: { high: 96.59, low: 2.93, avg: 37.198579, stddev: 17.867378, percentile: 0.010037 },
      cor3m: { high: 90.79, low: 7.19, avg: 41.240333, stddev: 16.35408, percentile: 0.012582 },
      cor6m: { high: 86.35, low: 9.67, avg: 44.342579, stddev: 15.781472, percentile: 0.0083 },
      cor1y: { high: 79.77, low: 11.9, avg: 46.133582, stddev: 14.713988, percentile: 0.007721 },
    },
    series,
    ...overrides,
  };
}

function hookState(partial: Partial<{
  data: CorData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
}> = {}) {
  return {
    data: null as CorData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseCor.mockReturnValue(state);
  return render(<CorPanel />);
}

describe("CorPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading Cboe implied correlation series")).toBeTruthy();
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
    expect(screen.getByText("No implied correlation data yet")).toBeTruthy();
  });
});

describe("CorPanel — header strip", () => {
  it("renders the four tenor values, the term spread, the percentile, and the source date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("7.38")).toBeTruthy(); // COR 1M
    expect(screen.getByText("10.48")).toBeTruthy(); // COR 3M
    expect(screen.getByText("12.16")).toBeTruthy(); // COR 6M
    expect(screen.getByText("14.19")).toBeTruthy(); // COR 1Y
    expect(screen.getByText("+6.81")).toBeTruthy(); // 1Y-1M spread
    expect(screen.getByText("0.8%")).toBeTruthy(); // 6M percentile
    expect(screen.getByText("07 Aug 2026")).toBeTruthy(); // newest Last-Modified
  });

  it("tones the regime cell by the 6M percentile regime", () => {
    renderPanel(hookState({ data: buildData() }));
    const regime = screen.getByTestId("cor-regime-value");
    expect(regime.textContent).toContain("COMPRESSED");
    expect(regime.style.color).toBe("var(--warning)");
  });
});

describe("CorPanel — chart", () => {
  it("renders the chart title and the tenor chips", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("SPX IMPLIED CORRELATION")).toBeTruthy();
    for (const label of ["1M", "3M", "6M", "1Y"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("does not emit NaN path coordinates with null-gap rows present", () => {
    renderPanel(hookState({ data: buildData() }));
    const paths = document.querySelectorAll("path");
    for (const path of Array.from(paths)) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});
