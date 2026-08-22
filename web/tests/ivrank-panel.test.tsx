/**
 * @vitest-environment jsdom
 *
 * IV RANK regime tab — SPY 30-day implied volatility ranked against its
 * trailing 252-session range.
 *
 * Pure helpers (lib/ivrank.ts): rank / IV-percent formatting, the strict
 * regime bands with their boundary pins, the display constants, and the
 * chart-row builder with nulls preserved.
 *
 * IvRankPanel: gating (SpectralLoader / SectionEmptyState), the summary
 * strip, the UW cross-check sub-label (renders when present, silently absent
 * when null — never an error state), the chart title, and the NaN guard.
 *
 * COPY PIN — no predictive claim. The tab is a descriptive richness/cheapness
 * read: a low rank means premium is cheap versus the past year, not that
 * volatility will rise. No rendered string may claim a forward move.
 *
 * Spec: docs/indicators/ivrank.md sections B.4, G.2.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  RANK_WINDOW,
  buildIvRankChartRows,
  formatIvPercent,
  formatRank,
  ivrankRegime,
  ivrankRegimeColor,
  type IvRankData,
  type IvRankEntry,
} from "@/lib/ivrank";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatRank — one-decimal rank", () => {
  it("formats the calibration rank", () => {
    expect(formatRank(10.559822)).toBe("10.6");
    expect(formatRank(100)).toBe("100.0");
    expect(formatRank(0)).toBe("0.0");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatRank(null)).toBe("---");
    expect(formatRank(undefined)).toBe("---");
    expect(formatRank(Number.NaN)).toBe("---");
  });
});

describe("formatIvPercent — decimal IV as a percent", () => {
  it("formats the calibration IV", () => {
    expect(formatIvPercent(0.12201147)).toBe("12.2%");
    expect(formatIvPercent(0.26251674)).toBe("26.3%");
  });

  it("returns --- for null and non-finite", () => {
    expect(formatIvPercent(null)).toBe("---");
    expect(formatIvPercent(Number.NaN)).toBe("---");
  });
});

describe("ivrankRegime — strict band boundaries (spec B.4)", () => {
  it("classifies the interior of each band", () => {
    expect(ivrankRegime(10.6)).toBe("SUPPRESSED");
    expect(ivrankRegime(35)).toBe("NORMAL");
    expect(ivrankRegime(65)).toBe("ELEVATED");
    expect(ivrankRegime(95)).toBe("EXTREME");
  });

  it("boundary values belong to the band above", () => {
    expect(ivrankRegime(19.999)).toBe("SUPPRESSED");
    expect(ivrankRegime(20)).toBe("NORMAL");
    expect(ivrankRegime(49.999)).toBe("NORMAL");
    expect(ivrankRegime(50)).toBe("ELEVATED");
    expect(ivrankRegime(79.999)).toBe("ELEVATED");
    expect(ivrankRegime(80)).toBe("EXTREME");
  });

  it("null and non-finite have no regime", () => {
    expect(ivrankRegime(null)).toBeNull();
    expect(ivrankRegime(Number.NaN)).toBeNull();
  });
});

describe("ivrankRegimeColor — brand tokens only", () => {
  it("maps bands to tokens, EXTREME to dislocation not negative", () => {
    expect(ivrankRegimeColor("SUPPRESSED")).toBe("var(--text-muted)");
    expect(ivrankRegimeColor("NORMAL")).toBe("var(--text-muted)");
    expect(ivrankRegimeColor("ELEVATED")).toBe("var(--warning)");
    expect(ivrankRegimeColor("EXTREME")).toBe("var(--dislocation)");
    expect(ivrankRegimeColor(null)).toBe("var(--text-muted)");
  });
});

describe("display constants", () => {
  it("mirrors the job's rank window", () => {
    expect(RANK_WINDOW).toBe(252);
  });
});

describe("buildIvRankChartRows — nulls preserved", () => {
  it("keeps null ranks as nulls, never coerces to 0", () => {
    const series: IvRankEntry[] = [
      { date: "2021-08-23", iv: 0.141, iv_rank: null, iv_pct: null },
      { date: "2026-08-21", iv: 0.12201147, iv_rank: 10.559822, iv_pct: 11.952191 },
    ];
    const rows = buildIvRankChartRows(series);
    expect(rows).toHaveLength(2);
    expect(rows[0].iv_rank).toBeNull();
    expect(rows[1].iv_rank).toBeCloseTo(10.559822, 6);
  });
});

/* ─── Panel ──────────────────────────────────────────── */

// jsdom ships no ResizeObserver; the d3 chart wires one up on mount.
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

const mockUseIvRank = vi.fn();
vi.mock("@/lib/useIvRank", () => ({
  useIvRank: (...args: unknown[]) => mockUseIvRank(...args),
}));

import IvRankPanel from "../components/IvRankPanel";

afterEach(() => {
  cleanup();
  mockUseIvRank.mockReset();
});

const DAY_MS = 86_400_000;
const SERIES_LENGTH = 90;
const SERIES_END_MS = Date.UTC(2026, 7, 21); // 2026-08-21, the calibration session

function seriesDate(index: number): string {
  return new Date(SERIES_END_MS - (SERIES_LENGTH - 1 - index) * DAY_MS)
    .toISOString()
    .slice(0, 10);
}

function buildSeries(length = SERIES_LENGTH): IvRankEntry[] {
  return Array.from({ length }, (_, i) => ({
    date: seriesDate(i),
    iv: 0.12 + 0.0002 * i,
    iv_rank: i < 10 ? null : 10 + (i % 50),
    iv_pct: i < 10 ? null : 12 + (i % 40),
  }));
}

function buildData(overrides: Partial<IvRankData> = {}): IvRankData {
  return {
    scan_time: new Date(SERIES_END_MS).toISOString(),
    status: "ok",
    source: "ib",
    as_of: "2026-08-21",
    expected_session: "2026-08-21",
    market_status: "closed",
    rank_window: 252,
    count: SERIES_LENGTH,
    rank_count: SERIES_LENGTH - 10,
    current: {
      date: "2026-08-21",
      iv: 0.12201147,
      iv_rank: 10.559822,
      iv_pct: 11.952191,
      iv_1y_low: 0.10542261,
      iv_1y_high: 0.26251674,
      rank_change_1d: -1.2,
      regime: "SUPPRESSED",
    },
    uw_check: { date: "2026-08-21", iv_rank: 10.58 },
    stats: {
      min: 0,
      p25: 18.4,
      median: 41.2,
      p75: 66,
      max: 100,
      mean: 43.1,
      share_suppressed: 0.24,
      share_extreme: 0.06,
    },
    series: buildSeries(),
    ...overrides,
  } as IvRankData;
}

type HookState = {
  data: IvRankData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: Date | null;
  refresh: () => void;
};

function hookState(overrides: Partial<HookState> = {}): HookState {
  return {
    data: buildData(),
    loading: false,
    syncing: false,
    error: null,
    lastSync: new Date(SERIES_END_MS),
    refresh: vi.fn(),
    ...overrides,
  };
}

describe("IvRankPanel — gating", () => {
  it("renders the SpectralLoader with its label while loading with no data", () => {
    mockUseIvRank.mockReturnValue(hookState({ data: null, loading: true }));
    render(<IvRankPanel />);
    expect(screen.getByText(/Loading SPY IV rank series/i)).toBeTruthy();
  });

  it("renders the empty state on missing:true", () => {
    mockUseIvRank.mockReturnValue(
      hookState({
        data: {
          missing: true,
          status: "missing",
          scan_time: null,
          as_of: null,
          count: 0,
          series: [],
          current: null,
          stats: null,
          uw_check: null,
        } as unknown as IvRankData,
      }),
    );
    render(<IvRankPanel />);
    expect(screen.getByText(/ivrank refresh timer/i)).toBeTruthy();
  });
});

describe("IvRankPanel — summary strip", () => {
  it("renders the calibration strip values", () => {
    mockUseIvRank.mockReturnValue(hookState());
    render(<IvRankPanel />);
    expect(screen.getByText("IV RANK")).toBeTruthy();
    expect(screen.getAllByText("10.6").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("12.2%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("SUPPRESSED")).toBeTruthy();
    // the 1y range cell carries both edges
    expect(screen.getByText(/10\.5%\s*-\s*26\.3%/)).toBeTruthy();
  });

  it("renders the UW cross-check sub-label when present", () => {
    mockUseIvRank.mockReturnValue(hookState());
    render(<IvRankPanel />);
    expect(screen.getByText(/UW CROSS-CHECK/i)).toBeTruthy();
  });

  it("omits the UW cross-check silently when null (never an error state)", () => {
    mockUseIvRank.mockReturnValue(hookState({ data: buildData({ uw_check: null }) }));
    render(<IvRankPanel />);
    expect(screen.queryByText(/UW CROSS-CHECK/i)).toBeNull();
    expect(screen.queryByText(/error/i)).toBeNull();
  });

  it("renders --- for a null-rank current (pre-window history)", () => {
    mockUseIvRank.mockReturnValue(
      hookState({
        data: buildData({
          current: {
            date: "2026-08-21",
            iv: 0.12201147,
            iv_rank: null,
            iv_pct: null,
            iv_1y_low: null,
            iv_1y_high: null,
            rank_change_1d: null,
            regime: null,
          },
        }),
      }),
    );
    render(<IvRankPanel />);
    expect(screen.getAllByText("---").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("SUPPRESSED")).toBeNull();
  });
});

describe("IvRankPanel — chart", () => {
  it("renders the chart title", () => {
    mockUseIvRank.mockReturnValue(hookState());
    render(<IvRankPanel />);
    expect(screen.getByText("SPY 1M IV RANK")).toBeTruthy();
  });

  it("emits no NaN into any svg path (null ranks are gaps, not zeros)", () => {
    mockUseIvRank.mockReturnValue(hookState());
    const { container } = render(<IvRankPanel />);
    const paths = Array.from(container.querySelectorAll("path"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

describe("IvRankPanel — copy discipline", () => {
  it("makes no predictive claim and hardcodes no cadence copy", () => {
    mockUseIvRank.mockReturnValue(hookState());
    const { container } = render(<IvRankPanel />);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/will rise|will fall|predicts|forecast/i);
    expect(text).not.toMatch(/refreshes (daily|hourly|5m)/i);
  });
});
