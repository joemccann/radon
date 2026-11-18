/**
 * @vitest-environment jsdom
 *
 * CURVE froze /regime navigation in production: every price tick re-rendered
 * YieldCurvePanel, which handed CriHistoryChart fresh `history` / `series`
 * arrays. The chart's d3 effect keys on those identities, so each tick tore
 * down and rebuilt ~18k dots (9,225 sessions x 2 series) in a 150-270 ms long
 * task. The router transition to any other tab never got a frame to commit.
 *
 * Contract: a parent re-render with unchanged data must hand the chart and
 * the brush the same array references.
 */
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { YieldCurveData, YieldCurvePoint } from "@/lib/yieldCurve";

const chartProps: Array<{ history: unknown; series: unknown }> = [];
vi.mock("../components/CriHistoryChart", () => ({
  default: (props: { history: unknown; series: unknown }) => {
    chartProps.push({ history: props.history, series: props.series });
    return null;
  },
}));

const brushProps: Array<{ values: unknown }> = [];
vi.mock("../components/BrushMinimap", () => ({
  default: (props: { values: unknown }) => {
    brushProps.push({ values: props.values });
    return null;
  },
}));

function buildSeries(count: number): YieldCurvePoint[] {
  return Array.from({ length: count }, (_, i) => ({
    date: new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
    y3m: 3.8,
    y2: 4 + i * 0.001,
    y10: 4.5 + i * 0.001,
    spread: 0.5,
    spx_close: 3000 + i,
  }));
}

const SERIES = buildSeries(60);
const DATA: YieldCurveData = {
  scan_time: "2026-11-17T22:32:26Z",
  source: "treasury",
  count: SERIES.length,
  current: { date: SERIES[SERIES.length - 1].date, y3m: 3.8, y2: 4.06, y10: 4.56, spread: 0.5 },
  series: SERIES,
};
const CURVE_STATE = { data: DATA, loading: false, syncing: false, error: null, lastSync: null, syncNow: vi.fn() };
const LIVE_STATE = { data: null, loading: false, syncing: false, error: null, lastSync: null, syncNow: vi.fn() };

vi.mock("@/lib/useYieldCurve", () => ({ useYieldCurve: () => CURVE_STATE }));
vi.mock("@/lib/useYieldCurveLive", () => ({ useYieldCurveLive: () => LIVE_STATE }));

import YieldCurvePanel from "../components/YieldCurvePanel";

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

afterEach(() => {
  cleanup();
  chartProps.length = 0;
  brushProps.length = 0;
});

function Parent({ tick }: { tick: number }) {
  return <div data-tick={tick}><YieldCurvePanel /></div>;
}

describe("YieldCurvePanel — chart inputs survive a parent re-render", () => {
  it("hands CriHistoryChart the same history and series on an unrelated re-render", () => {
    const { rerender } = render(<Parent tick={0} />);
    rerender(<Parent tick={1} />);

    expect(chartProps.length).toBeGreaterThanOrEqual(2);
    const first = chartProps[0];
    const last = chartProps[chartProps.length - 1];
    expect(last.history).toBe(first.history);
    expect(last.series).toBe(first.series);
  });

  it("hands BrushMinimap the same values on an unrelated re-render", () => {
    const { rerender } = render(<Parent tick={0} />);
    rerender(<Parent tick={1} />);

    expect(brushProps.length).toBeGreaterThanOrEqual(2);
    expect(brushProps[brushProps.length - 1].values).toBe(brushProps[0].values);
  });
});
