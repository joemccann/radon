/**
 * @vitest-environment jsdom
 *
 * VIX-COR regime tab — rolling 20-session correlation between the VIX close
 * and the Cboe 3-month SPX implied correlation close.
 *
 * Pure helpers (lib/vixcor.ts): signed correlation / VIX / percentile /
 * drawup formatting, the strict regime bands, the display constants, and the
 * chart-row builder with nulls preserved.
 *
 * VixCorPanel + VixCorChart: gating (SpectralLoader / SectionEmptyState), the
 * summary strip, the two-pane chart with its zero and 0.25 rules, the shaded
 * breakdown episodes including the live unresolved one, the parent-lag
 * degradation copy, and the base-rate block.
 *
 * EDITORIAL PIN — the reason this tab exists in this shape. The validation
 * study rejected the predictive half of the operator's premise: forward VIX
 * drawup after a breakdown comes in BELOW the unconditional base rate at
 * every horizon tested. The tab therefore ships as a descriptive correlation
 * regime read. These tests pin that: the base-rate column must render beside
 * the event column, no arrow annotation may exist on the chart, and no
 * predictive claim may appear in any rendered string. See
 * docs/indicators/vixcor.md section 0.
 *
 * Two spec conflicts resolved here, deliberately:
 *  1. G.6 bans the bare word "warning" while the sanctioned tooltip copy ends
 *     "not a warning", and bans "forecast" while the sanctioned base-rate
 *     footnote ends "not a forecast". The ban is on the CLAIM, not on the
 *     negation, so sanctioned negations ("not a warning" / "not a forecast" /
 *     "not a prediction") are stripped before the ban list is applied. Any
 *     other use of those words fails.
 *  2. F.2 says copy the cor route verbatim while F.4 says a snapshot beyond
 *     the 48h budget renders the empty state. The freshness gate wins (the
 *     skew / skew2d precedent); that is pinned in vixcor-api.test.ts.
 *
 * Spec: docs/indicators/vixcor.md sections G.2, G.4, G.5, G.6, G.7.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  BREAKDOWN_EXIT,
  BREAKDOWN_TRIGGER,
  CORR_WINDOW,
  buildVixcorChartRows,
  formatCorr,
  formatDrawup,
  formatPercentile,
  formatVix,
  vixcorRegime,
  vixcorRegimeColor,
  type VixcorData,
  type VixcorEntry,
  type VixcorEpisode,
} from "@/lib/vixcor";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatCorr — signed two-decimal correlation", () => {
  it("signs the calibration value and the coupled norm", () => {
    expect(formatCorr(0.014969)).toBe("+0.01");
    expect(formatCorr(0.9219)).toBe("+0.92");
    expect(formatCorr(0.8446)).toBe("+0.84");
  });

  it("signs negative correlations", () => {
    expect(formatCorr(-0.5324)).toBe("-0.53");
    expect(formatCorr(-0.0413)).toBe("-0.04");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatCorr(null)).toBe("---");
    expect(formatCorr(undefined)).toBe("---");
    expect(formatCorr(Number.NaN)).toBe("---");
  });
});

describe("formatVix — two-decimal index level", () => {
  it("formats the VIX close", () => {
    expect(formatVix(14.25)).toBe("14.25");
    expect(formatVix(16.7)).toBe("16.70");
  });

  it("returns --- for null and non-finite", () => {
    expect(formatVix(null)).toBe("---");
    expect(formatVix(Number.NaN)).toBe("---");
  });
});

describe("formatPercentile — fraction to one-decimal percent", () => {
  it("formats the all-history percentile and the 20d VIX coefficient of variation", () => {
    expect(formatPercentile(0.0181)).toBe("1.8%");
    expect(formatPercentile(0.0512)).toBe("5.1%");
    expect(formatPercentile(0.638)).toBe("63.8%");
  });

  it("returns --- for null", () => {
    expect(formatPercentile(null)).toBe("---");
  });
});

describe("formatDrawup — signed one-decimal percent", () => {
  it("formats the forward drawup columns", () => {
    expect(formatDrawup(0.0392)).toBe("+3.9%");
    expect(formatDrawup(0.0896)).toBe("+9.0%");
    expect(formatDrawup(0.3301)).toBe("+33.0%");
    expect(formatDrawup(0.4401)).toBe("+44.0%");
  });

  it("signs a negative drawup and returns --- for an unresolved horizon", () => {
    expect(formatDrawup(-0.052)).toBe("-5.2%");
    expect(formatDrawup(null)).toBe("---");
  });
});

describe("vixcorRegime — strict band inequalities", () => {
  it("below the 0.25 trigger reads DECOUPLED", () => {
    expect(vixcorRegime(0.014969)).toBe("DECOUPLED");
    expect(vixcorRegime(-0.5324)).toBe("DECOUPLED");
    expect(vixcorRegime(0.2499)).toBe("DECOUPLED");
  });

  it("0.25 up to 0.50 reads LOOSENING (the 0.25 boundary is not a trigger)", () => {
    expect(vixcorRegime(0.25)).toBe("LOOSENING");
    expect(vixcorRegime(0.4)).toBe("LOOSENING");
    expect(vixcorRegime(0.4999)).toBe("LOOSENING");
  });

  it("0.50 and above reads COUPLED", () => {
    expect(vixcorRegime(0.5)).toBe("COUPLED");
    expect(vixcorRegime(0.8)).toBe("COUPLED");
    expect(vixcorRegime(0.9932)).toBe("COUPLED");
  });

  it("a null or degenerate window has no regime", () => {
    expect(vixcorRegime(null)).toBeNull();
    expect(vixcorRegime(undefined)).toBeNull();
    expect(vixcorRegime(Number.NaN)).toBeNull();
  });
});

describe("vixcorRegimeColor — brand tones", () => {
  it("DECOUPLED is the dislocation token, not the fault token", () => {
    // A structural state, not a fault: --negative would be the UI over-claiming.
    expect(vixcorRegimeColor("DECOUPLED")).toBe("var(--dislocation)");
  });

  it("maps the remaining bands", () => {
    expect(vixcorRegimeColor("LOOSENING")).toBe("var(--warning)");
    expect(vixcorRegimeColor("COUPLED")).toBe("var(--text-muted)");
    expect(vixcorRegimeColor(null)).toBe("var(--text-muted)");
  });
});

describe("display constants — mirrored from the Python module, never recomputed", () => {
  it("pins the window and the two hysteresis thresholds", () => {
    expect(CORR_WINDOW).toBe(20);
    expect(BREAKDOWN_TRIGGER).toBe(0.25);
    expect(BREAKDOWN_EXIT).toBe(0.3);
  });
});

function chartEntry(overrides: Partial<VixcorEntry> = {}): VixcorEntry {
  return {
    date: "2026-08-14",
    vix_close: 14.25,
    cor3m_close: 12.34,
    corr20: 0.014969,
    episode: true,
    ...overrides,
  };
}

describe("buildVixcorChartRows — inclusive slice with nulls preserved", () => {
  const series: VixcorEntry[] = [
    chartEntry({ date: "2026-08-10", corr20: null, episode: false, vix_close: 15.5 }),
    chartEntry({ date: "2026-08-11", corr20: 0.233, episode: true, vix_close: 15.28 }),
    chartEntry({ date: "2026-08-12", corr20: 0.19, episode: true, vix_close: 15.0 }),
    chartEntry({ date: "2026-08-13", corr20: null, episode: true, vix_close: 14.86 }),
    chartEntry({ date: "2026-08-14", corr20: 0.014969, episode: true, vix_close: 14.25 }),
  ];

  it("returns the [start, end] slice inclusive of both ends", () => {
    const rows = buildVixcorChartRows(series, [1, 3]);
    expect(rows.map((r) => r.date)).toEqual(["2026-08-11", "2026-08-12", "2026-08-13"]);
  });

  it("keeps a null corr20 as a null (a path break, never a zero)", () => {
    const rows = buildVixcorChartRows(series, [0, 4]);
    expect(rows.map((r) => r.corr20)).toEqual([null, 0.233, 0.19, null, 0.014969]);
  });

  it("carries the VIX leg and the episode membership flag through", () => {
    const rows = buildVixcorChartRows(series, [0, 4]);
    expect(rows.map((r) => r.vix_close)).toEqual([15.5, 15.28, 15.0, 14.86, 14.25]);
    expect(rows.map((r) => r.episode)).toEqual([false, true, true, true, true]);
  });

  it("clamps an out-of-bounds range and tolerates an empty series", () => {
    expect(buildVixcorChartRows(series, [-5, 99])).toHaveLength(5);
    expect(buildVixcorChartRows([], [0, 0])).toEqual([]);
  });
});

/* ─── VixCorPanel ────────────────────────────────────── */

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

const mockUseVixcor = vi.fn();
vi.mock("@/lib/useVixcor", () => ({
  useVixcor: (...args: unknown[]) => mockUseVixcor(...args),
}));

import VixCorPanel from "../components/VixCorPanel";

afterEach(() => {
  cleanup();
  mockUseVixcor.mockReset();
});

const DAY_MS = 86_400_000;
const SERIES_LENGTH = 90;
const SERIES_END_MS = Date.UTC(2026, 7, 14); // 2026-08-14, the calibration session

function seriesDate(index: number): string {
  return new Date(SERIES_END_MS - (SERIES_LENGTH - 1 - index) * DAY_MS)
    .toISOString()
    .slice(0, 10);
}

const CLOSED_EPISODE: VixcorEpisode = {
  trigger: "2026-05-22",
  start: "2026-05-21",
  end: "2026-05-26",
  sessions: 4,
  trough: 0.235,
  trough_date: "2026-05-22",
  corr_at_trigger: 0.235,
  vix_at_trigger: 16.7,
  open: false,
  forward: { "5": 0.058, "10": 0.33, "21": 0.554, "42": 0.622, "63": null },
};

const OPEN_EPISODE: VixcorEpisode = {
  trigger: "2026-08-11",
  start: "2026-08-11",
  end: "2026-08-14",
  sessions: 4,
  trough: 0.014969,
  trough_date: "2026-08-14",
  corr_at_trigger: 0.233,
  vix_at_trigger: 15.28,
  open: true,
  forward: { "5": null, "10": null, "21": null, "42": null, "63": null },
};

const OPEN_DATES = new Set(["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]);
const CLOSED_DATES = new Set([
  "2026-05-21",
  "2026-05-22",
  "2026-05-23",
  "2026-05-24",
  "2026-05-25",
  "2026-05-26",
]);
const OPEN_TAIL: Record<string, { corr20: number; vix_close: number }> = {
  "2026-08-11": { corr20: 0.233, vix_close: 15.28 },
  "2026-08-12": { corr20: 0.19, vix_close: 15.0 },
  "2026-08-13": { corr20: 0.08, vix_close: 14.86 },
  "2026-08-14": { corr20: 0.014969, vix_close: 14.25 },
};

/**
 * 90 joined sessions ending on the calibration date. The first 19 rows carry
 * corr20: null (the leading-window rule) so the NaN-path guard has something
 * to bite on; the two episode date ranges are inside the span so the chart
 * has two bands to shade.
 */
function buildSeries(): VixcorEntry[] {
  return Array.from({ length: SERIES_LENGTH }, (_, i) => {
    const date = seriesDate(i);
    const tail = OPEN_TAIL[date];
    const inEpisode = OPEN_DATES.has(date) || CLOSED_DATES.has(date);
    return {
      date,
      vix_close: tail ? tail.vix_close : 14 + (i % 5) * 0.25,
      cor3m_close: 12 + (i % 3) * 0.5,
      corr20: i < 19 ? null : tail ? tail.corr20 : 0.8 + (i % 7) / 100,
      episode: inEpisode,
    };
  });
}

function buildData(overrides: Partial<VixcorData> = {}): VixcorData {
  const series = buildSeries();
  return {
    scan_time: "2026-08-16T02:35:11Z",
    source_last_modified: { vix: "Sat, 15 Aug 2026 23:01:11 GMT" },
    status: "ok",
    as_of: "2026-08-14",
    parent_as_of: "2026-08-14",
    vix_as_of: "2026-08-14",
    expected_session: "2026-08-14",
    lag_sessions: 0,
    market_status: "closed",
    window: 20,
    count: series.length,
    corr_count: series.length - 19,
    current: {
      date: "2026-08-14",
      vix_close: 14.25,
      cor3m_close: 12.34,
      corr20: 0.014969,
      change_1d: -0.0413,
      percentile: 0.0181,
      regime: "DECOUPLED",
      vix_cov_20d: 0.0512,
      episode: OPEN_EPISODE,
    },
    stats: {
      min: -0.5324,
      p01: -0.1135,
      p05: 0.2586,
      p10: 0.457,
      p25: 0.6795,
      median: 0.8446,
      p75: 0.925,
      p90: 0.9582,
      p95: 0.9723,
      p99: 0.9857,
      max: 0.9932,
      mean: 0.7621,
      stddev: 0.2341,
      share_below_zero: 0.0177,
      share_below_trigger: 0.0489,
      vix_cov_breakdown: 0.0691,
      vix_cov_coupled: 0.118,
    },
    episodes: [CLOSED_EPISODE, OPEN_EPISODE],
    /**
     * The real full-history aggregate, transcribed from the shipped payload
     * (scripts/tests/test_vixcor.py PIN_FORWARD_EVENT / PIN_FORWARD_BASE, both
     * derived from the checked-in fixtures). The medians matter: the earlier
     * hand-rounded placeholders had the h=42 event median BELOW the base
     * median, which is the opposite of what the data says and is exactly the
     * over-claim these tests exist to stop.
     */
    forward_stats: {
      horizons: [5, 10, 21, 42, 63],
      event: {
        "5": { n: 30, mean_drawup: 0.0392, median_drawup: 0.0377, p_higher: 0.4, p_drawup_20: 0.0667 },
        "10": { n: 30, mean_drawup: 0.08, median_drawup: 0.0765, p_higher: 0.4667, p_drawup_20: 0.1667 },
        "21": { n: 30, mean_drawup: 0.2089, median_drawup: 0.1448, p_higher: 0.5667, p_drawup_20: 0.3667 },
        "42": { n: 30, mean_drawup: 0.3301, median_drawup: 0.3179, p_higher: 0.6, p_drawup_20: 0.6333 },
        "63": { n: 29, mean_drawup: 0.4392, median_drawup: 0.3311, p_higher: 0.3793, p_drawup_20: 0.6897 },
      },
      base: {
        "5": { n: 5147, mean_drawup: 0.0896, median_drawup: 0.0546, p_higher: 0.4622, p_drawup_20: 0.1541 },
        "10": { n: 5142, mean_drawup: 0.1582, median_drawup: 0.1009, p_higher: 0.4619, p_drawup_20: 0.2816 },
        "21": { n: 5131, mean_drawup: 0.2736, median_drawup: 0.1786, p_higher: 0.458, p_drawup_20: 0.4592 },
        "42": { n: 5110, mean_drawup: 0.4401, median_drawup: 0.2931, p_higher: 0.4462, p_drawup_20: 0.638 },
        "63": { n: 5089, mean_drawup: 0.5727, median_drawup: 0.3939, p_higher: 0.4431, p_drawup_20: 0.7255 },
      },
    },
    series,
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: VixcorData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as VixcorData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseVixcor.mockReturnValue(state);
  return render(<VixCorPanel />);
}

const MISSING_DATA = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  episodes: [],
  current: null,
  stats: null,
  forward_stats: null,
} as unknown as VixcorData;

describe("VixCorPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading Cboe VIX and implied correlation series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the settled missing case", () => {
    renderPanel(hookState({ data: MISSING_DATA }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No VIX correlation data yet")).toBeTruthy();
  });

  it("shows the SectionEmptyState when the payload arrives with an empty series", () => {
    renderPanel(hookState({ data: buildData({ series: [], count: 0, corr_count: 0 }) }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });
});

describe("VixCorPanel — summary strip", () => {
  it("renders the current correlation and its one-day change", () => {
    renderPanel(hookState({ data: buildData() }));
    const cell = screen.getByTestId("vixcor-strip-corr");
    expect(cell.textContent).toContain("CORR 20D");
    expect(cell.textContent).toContain("+0.01");
    expect(cell.textContent).toContain("1D -0.04");
  });

  it("tones the regime cell DECOUPLED with the dislocation token", () => {
    renderPanel(hookState({ data: buildData() }));
    const regime = screen.getByTestId("vixcor-regime-value");
    expect(regime.textContent).toContain("DECOUPLED");
    expect(regime.style.color).toBe("var(--dislocation)");
    expect(screen.getByTestId("vixcor-strip-regime").textContent).toContain("PCTILE 1.8%");
  });

  it("renders LOOSENING and COUPLED off their own payloads", () => {
    const loosening = buildData();
    loosening.current!.corr20 = 0.4;
    loosening.current!.regime = "LOOSENING";
    renderPanel(hookState({ data: loosening }));
    expect(screen.getByTestId("vixcor-regime-value").textContent).toContain("LOOSENING");
    expect(screen.getByTestId("vixcor-regime-value").style.color).toBe("var(--warning)");
    cleanup();

    const coupled = buildData();
    coupled.current!.corr20 = 0.8;
    coupled.current!.regime = "COUPLED";
    renderPanel(hookState({ data: coupled }));
    expect(screen.getByTestId("vixcor-regime-value").textContent).toContain("COUPLED");
  });

  it("renders the VIX leg with its 20-day coefficient of variation", () => {
    renderPanel(hookState({ data: buildData() }));
    const cell = screen.getByTestId("vixcor-strip-vix");
    expect(cell.textContent).toContain("14.25");
    expect(cell.textContent).toContain("20D COV 5.1%");
  });

  it("renders the COR3M leg", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("vixcor-strip-cor3m").textContent).toContain("12.34");
  });

  it("renders the as-of session from the joined calendar", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("vixcor-strip-asof").textContent).toContain("2026-08-14");
  });
});

describe("VixCorPanel — episode cell", () => {
  it("names the live unresolved episode while it is open", () => {
    renderPanel(hookState({ data: buildData() }));
    const cell = screen.getByTestId("vixcor-strip-episode");
    expect(cell.textContent).toContain("2026-08-11");
    expect(cell.textContent).toContain("4 SESSIONS");
    expect(cell.textContent).toContain("TROUGH +0.01");
  });

  it("reads NONE with the last resolved episode when nothing is open", () => {
    const data = buildData({ episodes: [CLOSED_EPISODE] });
    data.current!.episode = null;
    renderPanel(hookState({ data }));
    const cell = screen.getByTestId("vixcor-strip-episode");
    expect(cell.textContent).toContain("NONE");
    expect(cell.textContent).toContain("LAST 2026-05-21");
  });
});

describe("VixCorPanel — parent-lag degradation copy is derived, never hardcoded", () => {
  it("reads the default sub-label when the parent is current", () => {
    // R-195: the old default was the literal "DAILY SERIES SINCE 2006-01" —
    // a hardcoded cadence claim standing in for a lag reading, on a branch
    // that ALSO absorbed null (lag could not be determined). This suite's own
    // title says the copy is derived and never hardcoded; now it is.
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("vixcor-lag-copy").textContent).toBe("PARENT CURRENT");
  });

  it("says the lag is unknown rather than claiming the parent is current", () => {
    renderPanel(hookState({ data: buildData({ lag_sessions: null }) }));
    expect(screen.getByTestId("vixcor-lag-copy").textContent).toBe("PARENT LAG UNKNOWN");
  });

  it("singularizes a one-session parent lag on the holding status", () => {
    renderPanel(
      hookState({
        data: buildData({ status: "holding", lag_sessions: 1, as_of: "2026-08-13", parent_as_of: "2026-08-13" }),
      }),
    );
    expect(screen.getByTestId("vixcor-lag-copy").textContent).toBe("PARENT 1 SESSION BEHIND");
  });

  it("pluralizes and warns beyond the grace window on stale_parent", () => {
    renderPanel(
      hookState({
        data: buildData({ status: "stale_parent", lag_sessions: 3, as_of: "2026-08-11", parent_as_of: "2026-08-11" }),
      }),
    );
    const copy = screen.getByTestId("vixcor-lag-copy");
    expect(copy.textContent).toBe("PARENT 3 SESSIONS BEHIND");
    expect(copy.style.color).toBe("var(--warning)");
  });

  it("still renders the chart and the strip while the parent lags", () => {
    renderPanel(hookState({ data: buildData({ status: "holding", lag_sessions: 1 }) }));
    expect(screen.getByTestId("vixcor-chart-section")).toBeTruthy();
    expect(screen.getByTestId("vixcor-strip-corr")).toBeTruthy();
  });

  it("hardcodes no freshness or cadence copy anywhere", () => {
    const { container } = renderPanel(hookState({ data: buildData(), lastSync: "2026-08-16T02:35:11Z" }));
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/refresh(es|ed)? (every|in) /i);
    expect(text).not.toMatch(/updated (hourly|daily|every)/i);
    expect(text).not.toMatch(/every \d+ (minute|hour|second)/i);
    expect(text).not.toMatch(/02:35/);
  });
});

describe("VixCorPanel — chart", () => {
  it("renders the two-pane chart with its title, range chips and brush", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("VIX VS 3-MONTH IMPLIED CORRELATION")).toBeTruthy();
    expect(screen.getByTestId("vixcor-chart-section")).toBeTruthy();
    expect(screen.getByTestId("vixcor-range-chips")).toBeTruthy();
    expect(screen.getByTestId("vixcor-brush")).toBeTruthy();
  });

  it("drops the brush when there are fewer than two sessions", () => {
    const one = buildSeries().slice(-1);
    renderPanel(hookState({ data: buildData({ series: one, count: 1, corr_count: 1 }) }));
    expect(screen.queryByTestId("vixcor-brush")).toBeNull();
  });

  it("draws the lower pane's zero rule and its 0.25 threshold rule", () => {
    renderPanel(hookState({ data: buildData() }));
    const zero = screen.getByTestId("vixcor-zero-line");
    const threshold = screen.getByTestId("vixcor-threshold-line");
    expect(zero).toBeTruthy();
    expect(threshold).toBeTruthy();
    // The threshold sits above zero on a downward-growing y axis.
    expect(Number(threshold.getAttribute("y1"))).toBeLessThan(Number(zero.getAttribute("y1")));
  });

  it("shades one band per visible episode and marks the open one", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getAllByTestId("vixcor-episode-shade")).toHaveLength(2);
    expect(screen.getAllByTestId("vixcor-episode-open")).toHaveLength(1);
  });

  it("marks no open episode once the last one has resolved", () => {
    const data = buildData({ episodes: [CLOSED_EPISODE] });
    data.current!.episode = null;
    renderPanel(hookState({ data }));
    expect(screen.getAllByTestId("vixcor-episode-shade")).toHaveLength(1);
    expect(screen.queryByTestId("vixcor-episode-open")).toBeNull();
  });

  it("draws no arrow annotation from a trough to a later VIX high", () => {
    // The operator's blue arrows are exactly the over-claim this tab refuses.
    const { container } = renderPanel(hookState({ data: buildData() }));
    expect(container.querySelectorAll("marker")).toHaveLength(0);
    expect(container.querySelector("[marker-end]")).toBeNull();
    expect(container.querySelector("[marker-start]")).toBeNull();
  });

  it("emits no NaN path coordinates with the null leading window present", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const paths = container.querySelectorAll("path");
    expect(paths.length).toBeGreaterThan(0);
    for (const path of Array.from(paths)) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

describe("VixCorPanel — base-rate block (the validation verdict, rendered)", () => {
  it("renders every horizon with both the event and the all-session column", () => {
    renderPanel(hookState({ data: buildData() }));
    const block = within(screen.getByTestId("vixcor-base-rate"));
    for (const horizon of ["5D", "10D", "21D", "42D", "63D"]) {
      expect(block.getByText(horizon)).toBeTruthy();
    }
    expect(screen.getByTestId("vixcor-base-rate").textContent).toContain("ALL SESSIONS");
    expect(screen.getByTestId("vixcor-base-rate").textContent?.toUpperCase()).toContain("BREAKDOWN");
  });

  it("shows the post-breakdown drawup beside the base rate it loses to", () => {
    renderPanel(hookState({ data: buildData() }));
    const text = screen.getByTestId("vixcor-base-rate").textContent ?? "";
    expect(text).toContain("+3.9%"); // event, h=5
    expect(text).toContain("+9.0%"); // base, h=5
    expect(text).toContain("+33.0%"); // event, h=42
    expect(text).toContain("+44.0%"); // base, h=42
  });

  it("sources the numbers from forward_stats rather than hardcoding them", () => {
    const data = buildData();
    data.forward_stats!.event["5"].mean_drawup = 0.1234;
    data.forward_stats!.base["5"].mean_drawup = 0.4321;
    renderPanel(hookState({ data }));
    const text = screen.getByTestId("vixcor-base-rate").textContent ?? "";
    expect(text).toContain("+12.3%");
    expect(text).toContain("+43.2%");
    expect(text).not.toContain("+3.9%");
  });

  it("labels itself as a regime description, not a forecast", () => {
    renderPanel(hookState({ data: buildData() }));
    const text = screen.getByTestId("vixcor-base-rate").textContent ?? "";
    expect(text).toContain("This is a regime description, not a forecast.");
  });
});

/* ─── The rebuttal may not over-claim either ─────────── */

/**
 * A mean-only rebuttal is itself an over-claim. The payload already carries
 * median_drawup and p_drawup_20, and on those two measures the event and the
 * base are indistinguishable at 42 sessions, which is precisely the horizon
 * the operator's four blue arrows span. The block must therefore show the
 * median and the +20% share beside the mean, and the footnote must say which
 * statistic the "below at every horizon" claim belongs to.
 */
describe("VixCorPanel — base-rate block shows the median and the +20% share", () => {
  function headerText(): string {
    const head = screen.getByTestId("vixcor-base-rate").querySelector("thead");
    return (head?.textContent ?? "").toUpperCase();
  }

  it("labels a mean column, a median column and a +20% column", () => {
    renderPanel(hookState({ data: buildData() }));
    const header = headerText();
    expect(header).toContain("MEAN");
    expect(header).toContain("MEDIAN");
    expect(header).toContain("+20%");
  });

  it("renders the event and base median at every horizon, derived from the payload", () => {
    const data = buildData();
    renderPanel(hookState({ data }));
    for (const horizon of data.forward_stats!.horizons) {
      const key = String(horizon);
      const row = screen.getByTestId(`vixcor-base-rate-row-${key}`).textContent ?? "";
      expect(row).toContain(formatDrawup(data.forward_stats!.event[key].median_drawup));
      expect(row).toContain(formatDrawup(data.forward_stats!.base[key].median_drawup));
    }
  });

  it("renders the event and base +20% share at every horizon, derived from the payload", () => {
    const data = buildData();
    renderPanel(hookState({ data }));
    for (const horizon of data.forward_stats!.horizons) {
      const key = String(horizon);
      const row = screen.getByTestId(`vixcor-base-rate-row-${key}`).textContent ?? "";
      expect(row).toContain(formatPercentile(data.forward_stats!.event[key].p_drawup_20));
      expect(row).toContain(formatPercentile(data.forward_stats!.base[key].p_drawup_20));
    }
  });

  /**
   * Hand-written literals, and the whole reason the qualification exists.
   * At h=42, from the shipped full-history payload:
   *   mean:    0.3301 event vs 0.4401 base   -> event is 11.00pp BELOW
   *   median:  0.3179 event vs 0.2931 base   -> event is  2.48pp ABOVE
   *            0.3179 - 0.2931 = 0.0248
   *   P(+20%): 0.6333 event vs 0.6380 base   -> 0.47pp apart, indistinguishable
   * Mann-Whitney at h=42 gives p = 0.683. Dropping the largest observation
   * leaves the event median at 0.3132, still above the 0.2931 base.
   */
  it("shows the h=42 median where the event sits ABOVE the base, not below", () => {
    const data = buildData();
    const event42 = data.forward_stats!.event["42"];
    const base42 = data.forward_stats!.base["42"];

    expect(event42.mean_drawup!).toBeLessThan(base42.mean_drawup!);
    expect(event42.median_drawup!).toBeGreaterThan(base42.median_drawup!);
    expect(event42.median_drawup! - base42.median_drawup!).toBeCloseTo(0.0248, 4);
    expect(Math.abs(event42.p_drawup_20! - base42.p_drawup_20!)).toBeLessThan(0.01);

    renderPanel(hookState({ data }));
    const row = screen.getByTestId("vixcor-base-rate-row-42").textContent ?? "";
    expect(row).toContain("+33.0%"); // event mean
    expect(row).toContain("+44.0%"); // base mean
    expect(row).toContain("+31.8%"); // event median, ABOVE the base median
    expect(row).toContain("+29.3%"); // base median
    expect(row).toContain("63.3%"); // event P(+20%)
    expect(row).toContain("63.8%"); // base P(+20%)
  });

  it("scopes the footnote claim to the mean and concedes the median at 42 sessions", () => {
    renderPanel(hookState({ data: buildData() }));
    const text = screen.getByTestId("vixcor-base-rate").textContent ?? "";
    expect(text).toContain("below the all-session mean at every horizon");
    expect(text).toContain("on the median the two are indistinguishable at 42 sessions");
    expect(text).not.toContain("below the all-session base rate at every horizon");
  });
});

/* ─── Over-claim and brand-token guards ──────────────── */

/**
 * The ban is on the CLAIM, not on its negation: the sanctioned copy says
 * "not a warning" and "not a forecast", which is the tab making exactly the
 * point these tests exist to protect. Strip those constructions first.
 */
function strippedOfSanctionedNegations(text: string): string {
  return text.replace(/\b(?:not|never)\s+(?:a|an)\s+(?:warning|forecast|prediction)\b/gi, "");
}

const BANNED_CLAIMS: ReadonlyArray<RegExp> = [
  /warning shot/i,
  /\bwarning\b/i,
  /\bpredicts?\b/i,
  /\bpredictive\b/i,
  /\bprediction\b/i,
  /signal ahead of/i,
  /\bforecasts?\b/i,
  /hit rate/i,
  /\b4[\s-](?:of|for)[\s-]4\b/i,
];

function assertNoOverClaim(text: string): void {
  const scanned = strippedOfSanctionedNegations(text);
  for (const banned of BANNED_CLAIMS) {
    expect(scanned).not.toMatch(banned);
  }
}

/** Every rendered string, including the attributes screen readers speak. */
function renderedText(container: HTMLElement): string {
  const attributes = Array.from(container.querySelectorAll("*"))
    .flatMap((el) => [el.getAttribute("aria-label"), el.getAttribute("title")])
    .filter((v): v is string => Boolean(v));
  return [container.textContent ?? "", ...attributes].join(" ");
}

describe("VixCorPanel — the copy stays descriptive", () => {
  it("makes no predictive claim in the visible panel", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    assertNoOverClaim(renderedText(container));
  });

  it("makes no predictive claim in the tooltip either", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const tooltip = container.querySelector('[data-sort-ignore="true"]');
    expect(tooltip).toBeTruthy();
    fireEvent.mouseEnter(tooltip as Element);
    const text = renderedText(container);
    // The tooltip must actually be open, or this guard is vacuous.
    expect(text.length).toBeGreaterThan(400);
    assertNoOverClaim(text);
  });

  it("uses no em dash in any user-facing string", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const tooltip = container.querySelector('[data-sort-ignore="true"]');
    if (tooltip) fireEvent.mouseEnter(tooltip);
    expect(renderedText(container)).not.toContain("—");
  });

  it("keeps the empty state descriptive too", () => {
    const { container } = renderPanel(hookState({ data: MISSING_DATA }));
    assertNoOverClaim(renderedText(container));
    expect(renderedText(container)).not.toContain("—");
  });
});

/**
 * Brand tokens only. A hex or rgba() literal is allowed ONLY as the fallback
 * inside `var(--token, fallback)` (chartSeriesColor emits exactly that), so
 * those wrappers are normalized away before the scan.
 */
function bareColorLiterals(container: HTMLElement): string[] {
  const offenders: string[] = [];
  for (const el of Array.from(container.querySelectorAll("*"))) {
    const values = [
      el.getAttribute("style"),
      el.getAttribute("fill"),
      el.getAttribute("stroke"),
    ].filter((v): v is string => Boolean(v));
    for (const value of values) {
      const normalized = value.replace(
        /var\(\s*--[\w-]+\s*,\s*(?:[^()]|\([^()]*\))*\)/g,
        "var(--token)",
      );
      for (const hit of normalized.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []) offenders.push(hit);
      for (const hit of normalized.match(/\brgba?\(/g) ?? []) offenders.push(hit);
    }
  }
  return offenders;
}

describe("VixCorPanel — brand tokens", () => {
  it("uses no raw hex and no rgba literal in the rendered panel", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    expect(bareColorLiterals(container)).toEqual([]);
  });

  it("uses no raw hex and no rgba literal in the degraded render", () => {
    const { container } = renderPanel(
      hookState({ data: buildData({ status: "stale_parent", lag_sessions: 3 }) }),
    );
    expect(bareColorLiterals(container)).toEqual([]);
  });
});
