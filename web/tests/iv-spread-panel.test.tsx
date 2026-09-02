/**
 * @vitest-environment jsdom
 *
 * IV SPREAD regime tab — NDX minus SPX 30-day ATM implied volatility, in
 * vol points, read against its own full-history mean and standard deviation.
 *
 * Pure helpers (lib/ivSpread.ts): spread / IV-percent / z formatting, the
 * strict regime bands with their boundary pins, the display constants, and
 * the chart-row builder with nulls preserved.
 *
 * IvSpreadPanel: gating (SpectralLoader / SectionEmptyState), the summary
 * strip, the STALE pill (renders on stale_source, absent on ok), the chart
 * title, the stats line, the NaN guard, the freshness rail countdown, and
 * the copy pins.
 *
 * COPY PIN — no predictive claim. The tab is a descriptive relative-premium
 * read. No rendered string may claim a forward move or a cadence.
 *
 * Spec: docs/indicators/iv-spread.md sections B.4, G.2, G.3.
 */
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  Z_COMPRESSED_MAX,
  Z_ELEVATED_MAX,
  Z_NORMAL_MAX,
  buildIvSpreadChartRows,
  formatIvPercent,
  formatSpread,
  formatZ,
  ivSpreadRegime,
  ivSpreadRegimeColor,
  type IvSpreadData,
  type IvSpreadEntry,
} from "@/lib/ivSpread";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatSpread — two-decimal vol points", () => {
  it("formats the calibration spread and the inversion", () => {
    expect(formatSpread(5.481468)).toBe("5.48");
    expect(formatSpread(-3.297135)).toBe("-3.30");
    expect(formatSpread(12.642458)).toBe("12.64");
    expect(formatSpread(0)).toBe("0.00");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatSpread(null)).toBe("---");
    expect(formatSpread(undefined)).toBe("---");
    expect(formatSpread(Number.NaN)).toBe("---");
  });
});

describe("formatIvPercent — decimal IV as a percent", () => {
  it("formats the calibration legs", () => {
    expect(formatIvPercent(0.1758578)).toBe("17.6%");
    expect(formatIvPercent(0.12104312)).toBe("12.1%");
  });

  it("returns --- for null and non-finite", () => {
    expect(formatIvPercent(null)).toBe("---");
    expect(formatIvPercent(Number.NaN)).toBe("---");
  });
});

describe("formatZ — signed two decimals", () => {
  it("signs both directions and zero", () => {
    expect(formatZ(0.104002)).toBe("+0.10");
    expect(formatZ(-1.25)).toBe("-1.25");
    expect(formatZ(0)).toBe("+0.00");
    expect(formatZ(2.5)).toBe("+2.50");
  });

  it("returns --- for null and non-finite", () => {
    expect(formatZ(null)).toBe("---");
    expect(formatZ(Number.NaN)).toBe("---");
  });
});

describe("ivSpreadRegime — strict band boundaries (spec B.4)", () => {
  it("classifies the interior of each band", () => {
    expect(ivSpreadRegime(-2.5)).toBe("COMPRESSED");
    expect(ivSpreadRegime(0.104002)).toBe("NORMAL");
    expect(ivSpreadRegime(1.5)).toBe("ELEVATED");
    expect(ivSpreadRegime(3)).toBe("EXTREME");
  });

  it("boundary values belong to the band above", () => {
    expect(ivSpreadRegime(-1.001)).toBe("COMPRESSED");
    expect(ivSpreadRegime(-1)).toBe("NORMAL");
    expect(ivSpreadRegime(0.999)).toBe("NORMAL");
    expect(ivSpreadRegime(1)).toBe("ELEVATED");
    expect(ivSpreadRegime(1.999)).toBe("ELEVATED");
    expect(ivSpreadRegime(2)).toBe("EXTREME");
  });

  it("null and non-finite have no regime", () => {
    expect(ivSpreadRegime(null)).toBeNull();
    expect(ivSpreadRegime(Number.NaN)).toBeNull();
  });
});

describe("ivSpreadRegimeColor — brand tokens only", () => {
  it("maps bands to tokens, EXTREME to dislocation not negative", () => {
    expect(ivSpreadRegimeColor("COMPRESSED")).toBe("var(--text-muted)");
    expect(ivSpreadRegimeColor("NORMAL")).toBe("var(--text-muted)");
    expect(ivSpreadRegimeColor("ELEVATED")).toBe("var(--warning)");
    expect(ivSpreadRegimeColor("EXTREME")).toBe("var(--dislocation)");
    expect(ivSpreadRegimeColor(null)).toBe("var(--text-muted)");
  });
});

describe("display constants", () => {
  it("mirror the job's z bands", () => {
    expect(Z_COMPRESSED_MAX).toBe(-1);
    expect(Z_NORMAL_MAX).toBe(1);
    expect(Z_ELEVATED_MAX).toBe(2);
  });
});

describe("buildIvSpreadChartRows — nulls preserved", () => {
  it("keeps an excluded session's null spread as null, never 0", () => {
    const series: IvSpreadEntry[] = [
      { date: "2026-08-17", spx_iv: 0.2443, ndx_iv: 0.18, spread: null },
      { date: "2026-09-02", spx_iv: 0.12104312, ndx_iv: 0.1758578, spread: 5.481468 },
    ];
    const rows = buildIvSpreadChartRows(series);
    expect(rows).toHaveLength(2);
    expect(rows[0].spread).toBeNull();
    expect(rows[1].spread).toBeCloseTo(5.481468, 6);
    expect(rows[1].spx_iv).toBeCloseTo(0.12104312, 8);
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

const mockUseIvSpread = vi.fn();
vi.mock("@/lib/useIvSpread", () => ({
  useIvSpread: (...args: unknown[]) => mockUseIvSpread(...args),
}));

import IvSpreadPanel from "../components/IvSpreadPanel";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  mockUseIvSpread.mockReset();
});

const DAY_MS = 86_400_000;
const SERIES_LENGTH = 90;
const SERIES_END_MS = Date.UTC(2026, 8, 2); // 2026-09-02, the calibration session

function seriesDate(index: number): string {
  return new Date(SERIES_END_MS - (SERIES_LENGTH - 1 - index) * DAY_MS)
    .toISOString()
    .slice(0, 10);
}

function buildSeries(length = SERIES_LENGTH): IvSpreadEntry[] {
  return Array.from({ length }, (_, i) => ({
    date: seriesDate(i),
    spx_iv: 0.12 + 0.0002 * (i % 13),
    ndx_iv: 0.17 + 0.0003 * (i % 11),
    // one excluded session in the middle: the chart must gap, never zero
    spread: i === 40 ? null : 5 + ((i % 9) - 4) * 0.4,
  }));
}

function buildData(overrides: Partial<IvSpreadData> = {}): IvSpreadData {
  return {
    scan_time: new Date(SERIES_END_MS).toISOString(),
    status: "ok",
    source: "ib",
    as_of: "2026-09-02",
    expected_session: "2026-09-02",
    market_status: "closed",
    count: SERIES_LENGTH,
    spread_count: SERIES_LENGTH - 1,
    dropped_unpaired: 0,
    current: {
      date: "2026-09-02",
      spx_iv: 0.12104312,
      ndx_iv: 0.1758578,
      spread: 5.481468,
      z_score: 0.104002,
      pctile: 59.377494,
      change_1d: 0.360352,
      regime: "NORMAL",
    },
    stats: {
      count: 1253,
      high: 12.642458,
      high_date: "2026-06-23",
      low: -3.297135,
      low_date: "2025-04-08",
      mean: 5.318448,
      stdev: 1.567474,
      last: 5.481468,
    },
    excluded: [],
    series: buildSeries(),
    ...overrides,
  } as IvSpreadData;
}

type HookState = {
  data: IvSpreadData | null;
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

const MISSING: IvSpreadData = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
  excluded: [],
} as unknown as IvSpreadData;

describe("IvSpreadPanel — gating", () => {
  it("renders the SpectralLoader with its label while loading with no data", () => {
    mockUseIvSpread.mockReturnValue(hookState({ data: null, loading: true }));
    render(<IvSpreadPanel />);
    expect(screen.getByText(/Loading NDX vs SPX IV spread series/i)).toBeTruthy();
  });

  it("renders the empty state on missing:true, naming the timer", () => {
    mockUseIvSpread.mockReturnValue(hookState({ data: MISSING }));
    render(<IvSpreadPanel />);
    expect(screen.getByText("No NDX vs SPX IV spread data yet")).toBeTruthy();
    expect(screen.getByText(/iv-spread refresh timer/i)).toBeTruthy();
  });
});

describe("IvSpreadPanel — summary strip", () => {
  it("renders the calibration strip values", () => {
    mockUseIvSpread.mockReturnValue(hookState());
    render(<IvSpreadPanel />);
    expect(screen.getByTestId("iv-spread-spread-value").textContent).toBe("5.48");
    expect(screen.getByTestId("iv-spread-strip-ndx").textContent).toContain("17.6%");
    expect(screen.getByTestId("iv-spread-strip-spx").textContent).toContain("12.1%");
    expect(screen.getByTestId("iv-spread-strip-z").textContent).toContain("+0.10");
    expect(screen.getByTestId("iv-spread-strip-pctile").textContent).toContain("59.4%");
    expect(screen.getByTestId("iv-spread-regime-value").textContent).toBe("NORMAL");
  });

  it("colours the spread and regime with the band token", () => {
    mockUseIvSpread.mockReturnValue(
      hookState({
        data: buildData({
          current: { ...buildData().current!, z_score: 2.4, regime: "EXTREME" },
        }),
      }),
    );
    render(<IvSpreadPanel />);
    expect(screen.getByTestId("iv-spread-regime-value").textContent).toBe("EXTREME");
    expect(screen.getByTestId("iv-spread-regime-value").getAttribute("style")).toContain(
      "var(--dislocation)",
    );
  });

  it("renders --- for a null-spread current (excluded session)", () => {
    mockUseIvSpread.mockReturnValue(
      hookState({
        data: buildData({
          current: {
            date: "2026-09-02",
            spx_iv: 0.2443,
            ndx_iv: 0.1758578,
            spread: null,
            z_score: null,
            pctile: null,
            change_1d: null,
            regime: null,
          },
        }),
      }),
    );
    render(<IvSpreadPanel />);
    expect(screen.getByTestId("iv-spread-spread-value").textContent).toBe("---");
    expect(screen.getByTestId("iv-spread-regime-value").textContent).toBe("---");
    expect(screen.queryByText("NORMAL")).toBeNull();
  });

  it("shows the STALE pill on stale_source and nothing on ok", () => {
    mockUseIvSpread.mockReturnValue(hookState());
    const ok = render(<IvSpreadPanel />);
    expect(ok.queryByTestId("iv-spread-degraded")).toBeNull();
    cleanup();

    mockUseIvSpread.mockReturnValue(hookState({ data: buildData({ status: "stale_source" }) }));
    render(<IvSpreadPanel />);
    expect(screen.getByTestId("iv-spread-degraded").textContent).toBe("STALE");
  });

  it("surfaces a background refresh failure", () => {
    mockUseIvSpread.mockReturnValue(hookState({ error: "fetch failed" }));
    render(<IvSpreadPanel />);
    expect(screen.getByTestId("iv-spread-refresh-error")).toBeTruthy();
  });
});

describe("IvSpreadPanel — freshness rail", () => {
  it("shows the payload date and counts down to the 22:15 UTC slot", () => {
    // 21:00 UTC on a Wednesday: 1h15m short of radon-iv-spread.timer's
    // 22:15 UTC slot. Any other schedule constant lands on a different number.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-26T21:00:00Z"));
    mockUseIvSpread.mockReturnValue(
      hookState({
        data: buildData({
          scan_time: "2026-08-26T21:00:00Z",
          as_of: "2026-08-25",
          current: { ...buildData().current!, date: "2026-08-25" },
        }),
      }),
    );
    render(<IvSpreadPanel />);
    act(() => {
      vi.advanceTimersByTime(0);
    });
    expect(screen.getByTestId("iv-spread-freshness-rail")).toBeTruthy();
    expect(screen.getByTestId("iv-spread-strip-asof").textContent).toBe("2026-08-25");
    expect(screen.getByTestId("iv-spread-freshness-rail-countdown").textContent).toBe("1h 15m");
    expect(screen.getByTestId("iv-spread-freshness-rail").textContent).toContain("Next sample");
  });
});

describe("IvSpreadPanel — chart", () => {
  it("renders the chart title and the stats line", () => {
    mockUseIvSpread.mockReturnValue(hookState());
    render(<IvSpreadPanel />);
    expect(screen.getByText("NDX VS SPX 1M ATM IMPLIED VOL SPREAD")).toBeTruthy();
    const stats = screen.getByTestId("iv-spread-stats").textContent ?? "";
    expect(stats).toContain("12.64");
    expect(stats).toContain("2026-06-23");
    expect(stats).toContain("-3.30");
    expect(stats).toContain("2025-04-08");
    expect(stats).toContain("5.32");
    expect(stats).toContain("1.57");
    expect(stats).toContain("5.48");
  });

  it("emits no NaN into any svg path (an excluded session is a gap, not a zero)", () => {
    mockUseIvSpread.mockReturnValue(hookState());
    const { container } = render(<IvSpreadPanel />);
    const paths = Array.from(container.querySelectorAll("path"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });

  it("renders the range chips and the brush", () => {
    mockUseIvSpread.mockReturnValue(hookState());
    render(<IvSpreadPanel />);
    expect(screen.getByTestId("iv-spread-range-chips")).toBeTruthy();
    expect(screen.getByTestId("iv-spread-brush")).toBeTruthy();
  });
});

describe("IvSpreadPanel — copy discipline", () => {
  it("makes no predictive claim, hardcodes no cadence copy, uses no em dash", () => {
    mockUseIvSpread.mockReturnValue(hookState());
    const { container } = render(<IvSpreadPanel />);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/will rise|will fall|predicts|forecast/i);
    expect(text).not.toMatch(/refreshes (daily|hourly|5m)/i);
    expect(text).not.toContain("—");
  });
});
