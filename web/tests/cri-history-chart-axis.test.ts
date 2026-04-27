import { describe, expect, it } from "vitest";
import {
  buildCriHistoryXAxisTickValues,
  shouldRotateCriHistoryXAxisLabels,
} from "../components/CriHistoryChart";

function makeDates(count: number): Date[] {
  return Array.from({ length: count }, (_, index) => new Date(Date.UTC(2026, 2, 25 + index)));
}

describe("CriHistoryChart x-axis helpers", () => {
  it("reduces 20-session history to a sparse explicit tick set on desktop widths", () => {
    const dates = makeDates(20);
    const ticks = buildCriHistoryXAxisTickValues(dates, 820);

    expect(ticks.length).toBeLessThanOrEqual(7);
    expect(ticks.length).toBeGreaterThanOrEqual(4);
    expect(ticks[0]?.getTime()).toBe(dates[0]?.getTime());
    expect(ticks.at(-1)?.getTime()).toBe(dates.at(-1)?.getTime());
  });

  it("keeps all labels when the data set is already small", () => {
    const dates = makeDates(5);
    const ticks = buildCriHistoryXAxisTickValues(dates, 820);

    expect(ticks).toHaveLength(5);
    expect(ticks.map((d) => d.getTime())).toEqual(dates.map((d) => d.getTime()));
  });

  it("rotates labels for dense or narrow layouts only", () => {
    expect(shouldRotateCriHistoryXAxisLabels(820, 6)).toBe(true);
    expect(shouldRotateCriHistoryXAxisLabels(520, 5)).toBe(true);
    expect(shouldRotateCriHistoryXAxisLabels(820, 4)).toBe(false);
  });
});
