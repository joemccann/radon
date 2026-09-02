/**
 * @vitest-environment jsdom
 *
 * MaRatioPanel — the MA RATIO regime tab (SPX pct above 50d MA over pct
 * above 200d MA, 0.25-0.5 signal zone, SPX log overlay, freshness rail).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, cleanup, within } from "@testing-library/react";

import {
  MA_RATIO_ZONE,
  isInSignalZone,
  maRatioStateLabel,
  maRatioZoneTurnUp,
  type MaRatioData,
  type MaRatioPoint,
} from "../lib/maRatio";
import { formatCountdown } from "../lib/freshnessRail";

/* ─── Environment stubs ─────────────────────────────────── */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as Record<string, unknown>).ResizeObserver = ResizeObserverStub;

const hookState = vi.hoisted(() => ({
  current: {
    data: null as MaRatioData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as Date | null,
    syncNow: () => {},
  },
}));

vi.mock("../lib/useMaRatio", () => ({
  useMaRatio: () => hookState.current,
}));

vi.mock("../lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, isDesktop: true, hasMounted: true }),
}));

import MaRatioPanel from "../components/MaRatioPanel";

/* ─── Fixtures ──────────────────────────────────────────── */

// The fake clock is pinned to a mid-week evening one hour before the 22:45
// UTC timer slot, with the payload holding that same completed session, so
// the rail reads "current" with a deterministic 1h countdown.
const FAKE_NOW_UTC = "2026-09-02T21:45:00Z"; // Wednesday, 17:45 ET
const SESSION_DATE = "2026-09-02";

function buildSeries(n: number): MaRatioPoint[] {
  const points: MaRatioPoint[] = [];
  const endMs = Date.parse(`${SESSION_DATE}T12:00:00Z`);
  for (let i = 0; i < n; i++) {
    const date = new Date(endMs - (n - 1 - i) * 86_400_000).toISOString().slice(0, 10);
    points.push({
      date,
      pct_above_50: 40 + (i % 20),
      pct_above_200: 60 + (i % 10),
      ratio: Number((0.6 + 0.4 * Math.abs(Math.sin(i / 25))).toFixed(4)),
      spx_close: 5000 + i * 10,
    });
  }
  return points;
}

function buildData(overrides: Partial<MaRatioData> = {}): MaRatioData {
  const series = buildSeries(120);
  const last = series[series.length - 1];
  return {
    schema_version: 1,
    scan_time: FAKE_NOW_UTC,
    data_date: last.date,
    source: { constituents: "cache", constituents_count: 503 },
    zone: { low: 0.25, high: 0.5 },
    current: {
      ...last,
      pct_above_50: 46.5,
      pct_above_200: 64.6,
      ratio: 0.72,
      count_above_50: 234,
      count_above_200: 325,
      eligible_50: 503,
      eligible_200: 503,
      spx_close: 7631.47,
    },
    series,
    missing: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(FAKE_NOW_UTC));
  hookState.current = {
    data: null,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: () => {},
  };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/* ─── Pure helper boundary pins ─────────────────────────── */

describe("maRatioStateLabel — band boundaries", () => {
  it("classifies each band with pinned boundaries", () => {
    expect(maRatioStateLabel(0.24)).toBe("WASHED OUT");
    expect(maRatioStateLabel(0.25)).toBe("SIGNAL ZONE"); // zone inclusive at 0.25
    expect(maRatioStateLabel(0.5)).toBe("SIGNAL ZONE"); // zone inclusive at 0.5
    expect(maRatioStateLabel(0.51)).toBe("50D LAGGING");
    expect(maRatioStateLabel(0.99)).toBe("50D LAGGING");
    expect(maRatioStateLabel(1.0)).toBe("50D LEADING");
  });

  it("the zone constants are the confirmed 0.25-0.5 band", () => {
    expect(MA_RATIO_ZONE.low).toBe(0.25);
    expect(MA_RATIO_ZONE.high).toBe(0.5);
  });
});

describe("maRatioZoneTurnUp — buy-style signal", () => {
  const pt = (date: string, ratio: number | null): MaRatioPoint => ({
    date,
    pct_above_50: 30,
    pct_above_200: 60,
    ratio,
    spx_close: 5000,
  });

  it("fires when the previous session sat in the zone and the latest turned strictly up", () => {
    expect(maRatioZoneTurnUp([pt("2026-08-31", 0.4), pt("2026-09-01", 0.45)])).toBe(true);
  });

  it("does not fire while the ratio keeps falling inside the zone", () => {
    expect(maRatioZoneTurnUp([pt("2026-08-31", 0.4), pt("2026-09-01", 0.35)])).toBe(false);
  });

  it("does not fire on a flat reading (strict inequality)", () => {
    expect(maRatioZoneTurnUp([pt("2026-08-31", 0.4), pt("2026-09-01", 0.4)])).toBe(false);
  });

  it("does not fire from outside the zone or on null ratios", () => {
    expect(maRatioZoneTurnUp([pt("2026-08-31", 0.6), pt("2026-09-01", 0.7)])).toBe(false);
    expect(maRatioZoneTurnUp([pt("2026-08-31", null), pt("2026-09-01", 0.3)])).toBe(false);
    expect(maRatioZoneTurnUp([pt("2026-09-01", 0.4)])).toBe(false);
  });

  it("isInSignalZone is inclusive at both edges and null-safe", () => {
    expect(isInSignalZone(0.25)).toBe(true);
    expect(isInSignalZone(0.5)).toBe(true);
    expect(isInSignalZone(0.2499)).toBe(false);
    expect(isInSignalZone(0.5001)).toBe(false);
    expect(isInSignalZone(null)).toBe(false);
  });
});

/* ─── Panel gates ───────────────────────────────────────── */

describe("MaRatioPanel — gates", () => {
  it("shows the loader while the first payload is in flight", () => {
    hookState.current.loading = true;
    const { container } = render(<MaRatioPanel />);
    expect(within(container).getByText("Loading SPX moving average breadth series")).toBeTruthy();
  });

  it("shows the empty state on missing:true", () => {
    hookState.current.data = {
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      series: [],
      zone: null,
    };
    const { container } = render(<MaRatioPanel />);
    expect(within(container).getByText("No MA ratio data yet")).toBeTruthy();
    expect(
      within(container).getByText(/the ma-ratio refresh timer/i),
    ).toBeTruthy();
  });
});

/* ─── Panel content ─────────────────────────────────────── */

describe("MaRatioPanel — content", () => {
  it("renders the strip values from the payload", () => {
    hookState.current.data = buildData();
    const { container } = render(<MaRatioPanel />);
    const q = within(container);
    expect(q.getByTestId("ma-ratio-value").textContent).toBe("0.72");
    expect(q.getByTestId("ma-ratio-state").textContent).toBe("50D LAGGING");
    expect(q.getByTestId("ma-ratio-pct50").textContent).toBe("46.5%");
    expect(q.getByTestId("ma-ratio-pct200").textContent).toBe("64.6%");
    expect(q.getByTestId("ma-ratio-signal").textContent).toBe("---");
  });

  it("labels a zone reading and a turn-up signal", () => {
    const series = buildSeries(120);
    series[series.length - 2] = { ...series[series.length - 2], ratio: 0.38 };
    series[series.length - 1] = { ...series[series.length - 1], ratio: 0.42 };
    hookState.current.data = buildData({
      series,
      current: {
        ...buildData().current!,
        ratio: 0.42,
      },
    });
    const { container } = render(<MaRatioPanel />);
    const q = within(container);
    expect(q.getByTestId("ma-ratio-state").textContent).toBe("SIGNAL ZONE");
    expect(q.getByTestId("ma-ratio-signal").textContent).toBe("TURN UP FROM ZONE");
  });

  it("renders the chart with the title, the zone band, and no NaN path", () => {
    hookState.current.data = buildData();
    const { container } = render(<MaRatioPanel />);
    const q = within(container);
    expect(q.getByText("SPX PCT ABOVE 50D MA / PCT ABOVE 200D MA")).toBeTruthy();
    expect(q.getByTestId("chart-reference-band")).toBeTruthy();
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d")).not.toContain("NaN");
    }
    expect(q.getByTestId("ma-ratio-brush")).toBeTruthy();
  });

  it("survives null ratio rows (zero-denominator guard) without NaN", () => {
    const series = buildSeries(120);
    series[50] = { ...series[50], ratio: null, spx_close: null };
    hookState.current.data = buildData({ series });
    const { container } = render(<MaRatioPanel />);
    for (const path of Array.from(container.querySelectorAll("path[d]"))) {
      expect(path.getAttribute("d")).not.toContain("NaN");
    }
  });

  it("mounts the freshness rail with the payload date and a live countdown", () => {
    hookState.current.data = buildData();
    const { container } = render(<MaRatioPanel />);
    const q = within(container);
    expect(q.getByTestId("ma-ratio-freshness-rail")).toBeTruthy();
    expect(q.getByTestId("ma-ratio-strip-asof").textContent).toBe(SESSION_DATE);
    // 21:45 UTC is one hour before the 22:45 UTC slot.
    expect(q.getByTestId("ma-ratio-freshness-rail-countdown").textContent).toBe(
      formatCountdown(60 * 60 * 1000),
    );
    expect(q.getByTestId("ma-ratio-freshness-rail").getAttribute("data-state")).toBe("current");
  });
});
