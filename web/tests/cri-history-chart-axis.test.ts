import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import {
  buildChartXAxisTickIndices,
  buildTimeXAxisTickValues,
  chartXAxisTickAnchor,
  resolveChartXAxisTickCount,
} from "../lib/chartXAxis";

function makeDates(count: number): Date[] {
  return Array.from({ length: count }, (_, index) => new Date(Date.UTC(2026, 2, 25 + index)));
}

function xPositions(dates: Date[], width: number): number[] {
  const first = dates[0]?.getTime() ?? 0;
  const last = dates.at(-1)?.getTime() ?? first;
  const span = Math.max(last - first, 1);
  return dates.map((date) => ((date.getTime() - first) / span) * width);
}

describe("CriHistoryChart x-axis helpers", () => {
  it("memoizes live-data merging so tooltip state does not rebuild the SVG", () => {
    const source = readFileSync(new URL("../components/CriHistoryChart.tsx", import.meta.url), "utf8");
    expect(source).toMatch(/const chartData = useMemo/);
    expect(source).toMatch(/\}, \[history, liveValues\]\);/);
    expect(source).not.toMatch(/\[chartData, width, series, liveValues,/);
  });
  it("reduces 20-session history to a sparse explicit tick set on desktop widths", () => {
    const dates = makeDates(20);
    const ticks = buildTimeXAxisTickValues(dates, 820);

    expect(ticks.length).toBeLessThanOrEqual(7);
    expect(ticks.length).toBeGreaterThanOrEqual(4);
    expect(ticks[0]?.getTime()).toBe(dates[0]?.getTime());
    expect(ticks.at(-1)?.getTime()).toBe(dates.at(-1)?.getTime());
  });

  it("keeps all labels when the data set is already small", () => {
    const dates = makeDates(5);
    const ticks = buildTimeXAxisTickValues(dates, 820);

    expect(ticks).toHaveLength(5);
    expect(ticks.map((d) => d.getTime())).toEqual(dates.map((d) => d.getTime()));
  });

  it("keeps ticks separated in rendered pixels when market-session gaps make timestamps irregular", () => {
    const dates = [
      "2026-09-03T14:27:00-04:00",
      "2026-09-03T15:27:00-04:00",
      "2026-09-04T09:27:00-04:00",
      "2026-09-04T10:27:00-04:00",
      "2026-09-04T11:27:00-04:00",
      "2026-09-04T12:27:00-04:00",
      "2026-09-04T13:27:00-04:00",
      "2026-09-04T14:27:00-04:00",
      "2026-09-08T09:27:00-04:00",
      "2026-09-08T10:27:00-04:00",
      "2026-09-08T11:27:00-04:00",
      "2026-09-08T12:27:00-04:00",
      "2026-09-08T13:27:00-04:00",
      "2026-09-08T14:27:00-04:00",
      "2026-09-08T15:27:00-04:00",
    ].map((value) => new Date(value));
    const width = 1_200;
    const ticks = buildTimeXAxisTickValues(dates, width, 180);
    const positions = xPositions(ticks, width);

    expect(ticks[0]?.getTime()).toBe(dates[0]?.getTime());
    expect(ticks.at(-1)?.getTime()).toBe(dates.at(-1)?.getTime());
    expect(Math.min(...positions.slice(1).map((position, index) => position - positions[index]))).toBeGreaterThanOrEqual(180);
  });

  it("drops invalid and duplicate timestamps before laying out ticks", () => {
    const valid = makeDates(5);
    const ticks = buildTimeXAxisTickValues(
      [valid[0], new Date(Number.NaN), valid[1], valid[1], valid[2], valid[3], valid[4]],
      820,
    );
    const times = ticks.map((date) => date.getTime());

    expect(times[0]).toBe(valid[0].getTime());
    expect(times.at(-1)).toBe(valid.at(-1)?.getTime());
    expect(times.every((time, index) => index === 0 || time > times[index - 1])).toBe(true);
  });

  it("aligns endpoint labels inward so they do not spill outside the plot", () => {
    expect(chartXAxisTickAnchor(0, 4)).toBe("start");
    expect(chartXAxisTickAnchor(1, 4)).toBe("middle");
    expect(chartXAxisTickAnchor(3, 4)).toBe("end");
  });

  it("drops index-based charts below four ticks when the viewport cannot fit them", () => {
    expect(resolveChartXAxisTickCount(20, 320)).toBe(3);
    expect(buildChartXAxisTickIndices(20, 320)).toEqual([0, 10, 19]);
    expect(buildChartXAxisTickIndices(20, 1_200)).toHaveLength(7);
  });
});
