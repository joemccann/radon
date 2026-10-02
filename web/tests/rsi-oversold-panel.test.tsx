/**
 * @vitest-environment jsdom
 *
 * RsiOversoldPanel — the RSI OVERSOLD regime tab (SPX pct of members with
 * Wilder RSI(14) below 30, 10% cluster threshold, SPX log overlay, freshness rail).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, cleanup, within } from "@testing-library/react";

import {
  RSI_OVERSOLD_THRESHOLD,
  rsiOversoldCrossUp,
  rsiOversoldStateLabel,
  type RsiOversoldData,
  type RsiOversoldPoint,
} from "../lib/rsiOversold";
import { formatCountdown } from "../lib/freshnessRail";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as Record<string, unknown>).ResizeObserver = ResizeObserverStub;

const hookState = vi.hoisted(() => ({
  current: {
    data: null as RsiOversoldData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as Date | null,
    syncNow: () => {},
  },
}));

vi.mock("../lib/useRsiOversold", () => ({
  useRsiOversold: () => hookState.current,
}));

vi.mock("../lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, isDesktop: true, hasMounted: true }),
}));

import RsiOversoldPanel from "../components/RsiOversoldPanel";

// Fake clock pinned one hour before the 23:05 UTC timer slot.
const FAKE_NOW_UTC = "2026-09-02T22:05:00Z";
const SESSION_DATE = "2026-09-02";

function buildSeries(n: number): RsiOversoldPoint[] {
  const points: RsiOversoldPoint[] = [];
  const endMs = Date.parse(`${SESSION_DATE}T12:00:00Z`);
  for (let i = 0; i < n; i++) {
    const date = new Date(endMs - (n - 1 - i) * 86_400_000).toISOString().slice(0, 10);
    points.push({
      date,
      pct_below_30: Number((2 + 8 * Math.abs(Math.sin(i / 25))).toFixed(4)),
      count_below_30: 10 + (i % 20),
      eligible: 500,
      spx_close: 5000 + i * 10,
    });
  }
  return points;
}

function buildData(overrides: Partial<RsiOversoldData> = {}): RsiOversoldData {
  const series = buildSeries(120);
  const last = series[series.length - 1];
  return {
    schema_version: 1,
    scan_time: FAKE_NOW_UTC,
    data_date: last.date,
    source: { constituents: "cache", constituents_count: 503 },
    threshold: 10.0,
    current: {
      ...last,
      pct_below_30: 12.4,
      count_below_30: 62,
      eligible: 500,
      spx_close: 6630.0,
      state: "OVERSOLD CLUSTER",
      cross_up: false,
      highest_since: "2026-03-13",
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

describe("rsiOversoldStateLabel — band boundaries", () => {
  it("classifies the 10% threshold with a strict inequality", () => {
    expect(rsiOversoldStateLabel(10)).toBe("NORMAL");
    expect(rsiOversoldStateLabel(10.001)).toBe("OVERSOLD CLUSTER");
    expect(rsiOversoldStateLabel(9.999)).toBe("NORMAL");
  });

  it("the threshold constant is 10", () => {
    expect(RSI_OVERSOLD_THRESHOLD).toBe(10);
  });
});

describe("rsiOversoldCrossUp", () => {
  const pt = (date: string, pct: number): RsiOversoldPoint => ({
    date,
    pct_below_30: pct,
    count_below_30: 50,
    eligible: 500,
    spx_close: 5000,
  });

  it("fires when previous is at or below 10 and latest is strictly above", () => {
    expect(rsiOversoldCrossUp([pt("2026-08-31", 10), pt("2026-09-01", 10.1)])).toBe(true);
  });

  it("does not fire when latest sits on 10", () => {
    expect(rsiOversoldCrossUp([pt("2026-08-31", 9.9), pt("2026-09-01", 10)])).toBe(false);
  });

  it("does not fire with a single point", () => {
    expect(rsiOversoldCrossUp([pt("2026-09-01", 12)])).toBe(false);
  });
});

describe("RsiOversoldPanel — gates", () => {
  it("shows the loader while the first payload is in flight", () => {
    hookState.current.loading = true;
    const { container } = render(<RsiOversoldPanel />);
    expect(within(container).getByText("Loading SPX RSI oversold breadth series")).toBeTruthy();
  });

  it("shows the empty state on missing:true", () => {
    hookState.current.data = {
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      series: [],
      threshold: null,
    };
    const { container } = render(<RsiOversoldPanel />);
    expect(within(container).getByText("No RSI oversold data yet")).toBeTruthy();
    expect(
      within(container).getByText(/the rsi-oversold refresh timer/i),
    ).toBeTruthy();
  });
});

describe("RsiOversoldPanel — content", () => {
  it("renders the strip values from the payload", () => {
    hookState.current.data = buildData();
    const { container } = render(<RsiOversoldPanel />);
    const q = within(container);
    expect(q.getByTestId("rsi-oversold-value").textContent).toBe("12.4%");
    expect(q.getByTestId("rsi-oversold-state").textContent).toBe("OVERSOLD CLUSTER");
    expect(q.getByTestId("rsi-oversold-members").textContent).toBe("62 / 500");
    expect(q.getByTestId("rsi-oversold-highest").textContent).toBe("2026-03-13");
  });

  it("labels a reading at the 10% boundary as NORMAL", () => {
    hookState.current.data = buildData({
      current: {
        ...buildData().current!,
        pct_below_30: 10,
        state: "NORMAL",
      },
    });
    const { container } = render(<RsiOversoldPanel />);
    expect(within(container).getByTestId("rsi-oversold-state").textContent).toBe("NORMAL");
  });

  it("renders the chart with the title, the threshold line, and no NaN path", () => {
    hookState.current.data = buildData();
    const { container } = render(<RsiOversoldPanel />);
    const q = within(container);
    expect(q.getByText("SPX PCT OF MEMBERS WITH RSI(14) BELOW 30")).toBeTruthy();
    expect(q.getByText("10% oversold cluster threshold")).toBeTruthy();
    expect(q.getByTestId("chart-reference-band")).toBeTruthy();
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d")).not.toContain("NaN");
    }
    expect(q.getByTestId("rsi-oversold-brush")).toBeTruthy();
  });

  it("survives a NaN pct row without NaN in a path", () => {
    const series = buildSeries(120);
    series[50] = { ...series[50], pct_below_30: Number.NaN, spx_close: null };
    hookState.current.data = buildData({ series });
    const { container } = render(<RsiOversoldPanel />);
    for (const path of Array.from(container.querySelectorAll("path[d]"))) {
      expect(path.getAttribute("d")).not.toContain("NaN");
    }
  });

  it("mounts the freshness rail with the payload date and a live countdown", () => {
    hookState.current.data = buildData();
    const { container } = render(<RsiOversoldPanel />);
    const q = within(container);
    expect(q.getByTestId("rsi-oversold-freshness-rail")).toBeTruthy();
    expect(q.getByTestId("rsi-oversold-strip-asof").textContent).toBe(SESSION_DATE);
    expect(q.getByTestId("rsi-oversold-freshness-rail-countdown").textContent).toBe(
      formatCountdown(60 * 60 * 1000),
    );
    expect(q.getByTestId("rsi-oversold-freshness-rail").getAttribute("data-state")).toBe("current");
  });
});
