/**
 * @vitest-environment jsdom
 *
 * CalmStreakPanel: the CALM STREAK regime tab (consecutive SPX sessions
 * without a >1% intraday band, SPX log overlay, freshness rail).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, cleanup, within } from "@testing-library/react";

import {
  CALM_PERCENTILE,
  CALM_STREAK_THRESHOLD_PCT,
  calmStreakStateLabel,
  formatBandPct,
  type CalmStreakData,
  type CalmStreakPoint,
} from "../lib/calmStreak";
import { formatCountdown } from "../lib/freshnessRail";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as Record<string, unknown>).ResizeObserver = ResizeObserverStub;

const hookState = vi.hoisted(() => ({
  current: {
    data: null as CalmStreakData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as Date | null,
    syncNow: () => {},
  },
}));

vi.mock("../lib/useCalmStreak", () => ({
  useCalmStreak: () => hookState.current,
}));

vi.mock("../lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, isDesktop: true, hasMounted: true }),
}));

import CalmStreakPanel from "../components/CalmStreakPanel";

// Tuesday 13:30 UTC (09:30 ET): one hour before the 14:30 UTC slot, and the
// last completed session is Monday 2026-09-14, so the rail reads current.
const FAKE_NOW_UTC = "2026-09-15T13:30:00Z";
const SESSION_DATE = "2026-09-14";

function buildSeries(n: number): CalmStreakPoint[] {
  const endMs = Date.parse(`${SESSION_DATE}T12:00:00Z`);
  return Array.from({ length: n }, (_, i) => ({
    date: new Date(endMs - (n - 1 - i) * 7 * 86_400_000).toISOString().slice(0, 10),
    streak: (i * 7) % 40,
    close: 1000 + i * 5,
  }));
}

function buildData(overrides: Partial<CalmStreakData> = {}): CalmStreakData {
  return {
    schema_version: 1,
    scan_time: FAKE_NOW_UTC,
    data_date: SESSION_DATE,
    source_last_modified: "Tue, 15 Sep 2026 13:02:41 GMT",
    source: { name: "cboe", url: "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_SPX.json" },
    threshold_pct: 1,
    current: { date: SESSION_DATE, streak: 28, band_pct: 0.7312, close: 7619.98 },
    stats: {
      max: { streak: 64, date: "2017-03-20" },
      window: { start: "1996-01-01", end: "2016-12-31", streak: 26, date: "2014-06-23" },
      percentile: 98.9,
    },
    series: buildSeries(300),
    missing: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(FAKE_NOW_UTC));
  hookState.current = { data: null, loading: false, syncing: false, error: null, lastSync: null, syncNow: () => {} };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("calmStreakStateLabel", () => {
  it("pins the boundaries", () => {
    expect(CALM_STREAK_THRESHOLD_PCT).toBe(1);
    expect(CALM_PERCENTILE).toBe(90);
    expect(calmStreakStateLabel(27, 26, 50)).toBe("EXTREME CALM");
    expect(calmStreakStateLabel(26, 26, 97)).toBe("CALM"); // equal to window max is not extreme
    expect(calmStreakStateLabel(12, 26, 90)).toBe("CALM"); // percentile inclusive at 90
    expect(calmStreakStateLabel(12, 26, 89.9)).toBe("NORMAL");
  });

  it("formatBandPct is null-safe", () => {
    expect(formatBandPct(0.7312)).toBe("0.73%");
    expect(formatBandPct(null)).toBe("---");
    expect(formatBandPct(Number.NaN)).toBe("---");
  });
});

describe("CalmStreakPanel gates", () => {
  it("shows the loader while the first payload is in flight", () => {
    hookState.current.loading = true;
    const { container } = render(<CalmStreakPanel />);
    expect(within(container).getByText("Loading SPX intraday band series")).toBeTruthy();
  });

  it("shows the empty state on missing:true", () => {
    hookState.current.data = {
      missing: true, scan_time: null, data_date: null, current: null, stats: null, series: [],
    } as unknown as CalmStreakData;
    const { container } = render(<CalmStreakPanel />);
    const q = within(container);
    expect(q.getByText("No calm streak data yet")).toBeTruthy();
    expect(q.getByText(/the calm-streak refresh timer/i)).toBeTruthy();
  });
});

describe("CalmStreakPanel content", () => {
  it("renders the strip values", () => {
    hookState.current.data = buildData();
    const { container } = render(<CalmStreakPanel />);
    const q = within(container);
    expect(q.getByTestId("calm-streak-value").textContent).toBe("28");
    expect(q.getByTestId("calm-streak-state").textContent).toBe("EXTREME CALM");
    expect(q.getByTestId("calm-streak-band").textContent).toBe("0.73%");
    expect(q.getByTestId("calm-streak-window-max").textContent).toBe("26");
    expect(q.getByTestId("calm-streak-max").textContent).toBe("64");
    expect(q.getByTestId("calm-streak-percentile").textContent).toBe("98.9%");
  });

  it("renders the chart title, brush, and no NaN path", () => {
    hookState.current.data = buildData();
    const { container } = render(<CalmStreakPanel />);
    const q = within(container);
    expect(q.getByText("CONSECUTIVE SESSIONS WITHOUT A >1% INTRADAY BAND")).toBeTruthy();
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const p of paths) expect(p.getAttribute("d")).not.toContain("NaN");
    expect(q.getByTestId("calm-streak-brush")).toBeTruthy();
  });

  it("survives null closes without NaN", () => {
    const series = buildSeries(300).map((p, i) => (i % 50 === 0 ? { ...p, close: null } : p));
    hookState.current.data = buildData({ series });
    const { container } = render(<CalmStreakPanel />);
    for (const p of Array.from(container.querySelectorAll("path[d]"))) {
      expect(p.getAttribute("d")).not.toContain("NaN");
    }
  });

  it("mounts the freshness rail with the payload date and a live countdown", () => {
    hookState.current.data = buildData();
    const { container } = render(<CalmStreakPanel />);
    const q = within(container);
    expect(q.getByTestId("calm-streak-freshness-rail")).toBeTruthy();
    expect(q.getByTestId("calm-streak-strip-asof").textContent).toBe(SESSION_DATE);
    expect(q.getByTestId("calm-streak-freshness-rail-countdown").textContent).toBe(formatCountdown(60 * 60 * 1000));
    expect(q.getByTestId("calm-streak-freshness-rail").getAttribute("data-state")).toBe("current");
  });
});
