/**
 * @vitest-environment jsdom
 *
 * STRADDLE regime tab — SPX realized vs implied 1-day straddle ratio.
 *
 * Pure helpers (lib/straddle.ts): signed ratio formatting, strict-inequality
 * breakeven tones, straddle %% / hit-rate / source-date formatting, chart-row
 * selection with nulls preserved.
 *
 * StraddlePanel: gating (SpectralLoader / SectionEmptyState), the header stat
 * strip, the chart title, and the NaN path guard. Spec:
 * docs/indicators/straddle.md.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  buildStraddleChartRows,
  formatHitRate,
  formatRatio,
  formatSourceDate,
  formatStraddlePct,
  ratioColor,
  type StraddleData,
  type StraddleEntry,
} from "@/lib/straddle";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatRatio — signed two-decimal display", () => {
  it("signs positive and negative ratios", () => {
    expect(formatRatio(3.775801)).toBe("+3.78");
    expect(formatRatio(-4.968372)).toBe("-4.97");
    expect(formatRatio(0.063255)).toBe("+0.06");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatRatio(null)).toBe("---");
    expect(formatRatio(undefined)).toBe("---");
    expect(formatRatio(Number.NaN)).toBe("---");
  });
});

describe("ratioColor — strict breakeven inequalities", () => {
  it("beyond +1 the realized move beat the straddle upside", () => {
    expect(ratioColor(1.01)).toBe("var(--positive)");
    expect(ratioColor(3.78)).toBe("var(--positive)");
  });

  it("beyond -1 the downside overshoot reads negative", () => {
    expect(ratioColor(-1.01)).toBe("var(--negative)");
    expect(ratioColor(-4.97)).toBe("var(--negative)");
  });

  it("the boundary and the inside band read muted (strict inequalities)", () => {
    expect(ratioColor(1.0)).toBe("var(--text-muted)");
    expect(ratioColor(-1.0)).toBe("var(--text-muted)");
    expect(ratioColor(0)).toBe("var(--text-muted)");
    expect(ratioColor(null)).toBe("var(--text-muted)");
  });
});

describe("formatStraddlePct / formatHitRate", () => {
  it("formats the implied 1-day straddle as a percentage", () => {
    expect(formatStraddlePct(0.473971)).toBe("0.47%");
    expect(formatStraddlePct(null)).toBe("---");
  });

  it("formats the breakeven hit rate from a fraction", () => {
    expect(formatHitRate(0.367675)).toBe("36.8%");
    expect(formatHitRate(null)).toBe("---");
  });
});

describe("formatSourceDate", () => {
  it("formats the source Last-Modified header as a compact date", () => {
    expect(formatSourceDate("Wed, 05 Aug 2026 18:31:25 GMT")).toBe("05 Aug 2026");
    expect(formatSourceDate(null)).toBe("---");
    expect(formatSourceDate("not-a-date")).toBe("not-a-date");
  });
});

function entry(overrides: Partial<StraddleEntry> = {}): StraddleEntry {
  return {
    date: "2026-08-04",
    spx_close: 7736.52,
    vix1d_close: 13.86,
    ratio: 3.775801,
    ...overrides,
  };
}

describe("buildStraddleChartRows — nulls preserved", () => {
  it("maps ratio to the right series and SPX to the overlay", () => {
    const rows = buildStraddleChartRows([
      entry({ date: "2022-05-13", spx_close: 4023.89, vix1d_close: 33.63, ratio: null }),
      entry({ date: "2022-05-16", spx_close: 4008.01, vix1d_close: 30.11, ratio: -0.233474 }),
      entry(),
    ]);
    expect(rows.map((r) => r.date)).toEqual(["2022-05-13", "2022-05-16", "2026-08-04"]);
    expect(rows.map((r) => r.ratio)).toEqual([null, -0.233474, 3.775801]);
    expect(rows.map((r) => r.spx)).toEqual([4023.89, 4008.01, 7736.52]);
  });
});

/* ─── StraddlePanel component ────────────────────────── */

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

const mockUseStraddle = vi.fn();
vi.mock("@/lib/useStraddle", () => ({
  useStraddle: (...args: unknown[]) => mockUseStraddle(...args),
}));

import StraddlePanel from "../components/StraddlePanel";

afterEach(() => {
  cleanup();
  mockUseStraddle.mockReset();
});

function buildSeries(count: number): StraddleEntry[] {
  return Array.from({ length: count }, (_, i) => {
    const day = new Date(Date.UTC(2026, 0, 5 + i));
    return entry({
      date: day.toISOString().slice(0, 10),
      spx_close: 6000 + i * 10,
      vix1d_close: 12 + (i % 5),
      ratio: i === 0 ? null : ((i % 7) - 3) * 0.9,
    });
  });
}

function buildData(overrides: Partial<StraddleData> = {}): StraddleData {
  const series = buildSeries(120);
  return {
    scan_time: "2026-08-05T02:15:00Z",
    source_last_modified: {
      spx: "Wed, 05 Aug 2026 17:01:43 GMT",
      vix1d: "Wed, 05 Aug 2026 18:31:25 GMT",
    },
    count: series.length,
    current: {
      date: "2026-08-04",
      ratio: 3.775801,
      move_pct: 1.789619,
      implied_straddle_pct: 0.473971,
      spx_close: 7736.52,
      vix1d_prior: 9.43,
    },
    stats: { high: 3.775801, low: -4.968372, avg: 0.063255, stddev: 1.157428, hit_rate: 0.367675 },
    series,
    ...overrides,
  };
}

function hookState(partial: Partial<{
  data: StraddleData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
}> = {}) {
  return {
    data: null as StraddleData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseStraddle.mockReturnValue(state);
  return render(<StraddlePanel />);
}

describe("StraddlePanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading Cboe straddle series")).toBeTruthy();
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
    expect(screen.getByText("No straddle data yet")).toBeTruthy();
  });
});

describe("StraddlePanel — header strip", () => {
  it("renders last ratio, stats, implied straddle, and source date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("+3.78")).toBeTruthy(); // last ratio
    expect(screen.getByText("-4.97")).toBeTruthy(); // low
    expect(screen.getByText("36.8%")).toBeTruthy(); // breakeven hit rate
    expect(screen.getByText("0.47%")).toBeTruthy(); // implied 1d straddle
    expect(screen.getByText("05 Aug 2026")).toBeTruthy(); // vix1d Last-Modified
  });

  it("tones the last ratio by the strict breakeven inequality", () => {
    renderPanel(hookState({ data: buildData() }));
    const last = screen.getByTestId("straddle-last-value");
    expect(last.style.color).toBe("var(--positive)");
  });
});

describe("StraddlePanel — chart", () => {
  it("renders the chart with the ratio right series", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("SPX VS 1-DAY STRADDLE RATIO")).toBeTruthy();
  });

  it("does not emit NaN path coordinates with null-ratio rows present", () => {
    renderPanel(hookState({ data: buildData() }));
    const paths = document.querySelectorAll("path");
    for (const path of Array.from(paths)) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});
