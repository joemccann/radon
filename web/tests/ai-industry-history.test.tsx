// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { aiCalendarPresets, aiCalendarRange, aiHistoryGapMs, aiPeriodicBars, comparableAiHistory } from "@/lib/aiIndustryHistory";
import type { AiHistoryPoint } from "@/lib/aiInfrastructure";
import CriHistoryChart from "@/components/CriHistoryChart";
import BrushMinimap from "@/components/BrushMinimap";
import AiIndustryHistoryChart from "@/components/AiIndustryHistoryChart";
const point = (date: string, value = 10): AiHistoryPoint => ({ date, value, label: "Routed tokens", unit: "tokens", series_id: "tokens:v1:fixed", source_id: "publisher" });
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 800, height: 440, top: 0, left: 0, right: 800, bottom: 440, x: 0, y: 0, toJSON: () => ({}) });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("AI observation history integrity", () => {
  it("uses calendar months for seven-day-a-week publisher observations", () => {
    const history = Array.from({ length: 90 }, (_, i) => point(new Date(Date.UTC(2026, 0, i + 1)).toISOString()));
    const [start, end] = aiCalendarRange(history, "1m");
    expect(history[start].date.slice(0, 10)).toBe("2026-02-28");
    expect(history[end].date.slice(0, 10)).toBe("2026-03-31");
    expect(end - start + 1).toBe(32);
  });
  it("does not apply 63 trading sessions to monthly observations", () => {
    const history = Array.from({ length: 9 }, (_, i) => point(`2026-${String(i + 1).padStart(2, "0")}-01`));
    expect(aiCalendarRange(history, "3m")).toEqual([5, 8]);
    expect(aiCalendarPresets(history).map(p => p.slug)).toEqual(["1m", "3m", "6m", "all"]);
  });
  it("rejects mixed units or methodology cohorts and drops invalid observations", () => {
    expect(comparableAiHistory([point("2026-01-01"), { ...point("2026-01-02"), unit: "USD" }])).toEqual([]);
    expect(comparableAiHistory([point("2026-01-01"), { ...point("2026-01-02"), series_id: "tokens:v2:other" }])).toEqual([]);
    expect(comparableAiHistory([point("bad"), point("2026-01-02", Number.NaN), point("2026-01-03")])).toHaveLength(1);
  });
  it("honors daily cadence with sparse history and recognizes two quarterly filings", () => {
    const sparse = [point("2026-01-01"), point("2026-03-01")];
    expect(aiHistoryGapMs(sparse, "daily")).toBe(1.8 * 86_400_000);
    expect(aiPeriodicBars([point("2026-03-31"), point("2026-06-30")], "filing")).toBe(true);
  });
  it("shows a single observation honestly without chart or brush", () => {
    render(<AiIndustryHistoryChart points={[point("2026-01-01")]} />);
    expect(screen.getByText(/A trend needs at least two comparable dates/)).toBeTruthy();
    expect(screen.queryByTestId("ai-history-brush")).toBeNull();
  });
});

describe("shared Regime renderer for AI series", () => {
  it("renders one genuine legend/axis and permits keyboard observation inspection", () => {
    const { container } = render(<CriHistoryChart history={[point("2026-01-01", 10), point("2026-01-02", 12)]} series={[{ key: "value", label: "Tokens", color: "green", axis: "left" }]} title="Usage" />);
    expect(container.querySelectorAll(".chart-legend-item")).toHaveLength(1);
    const slider = screen.getByRole("slider", { name: "Inspect Usage history" });
    fireEvent.keyDown(slider, { key: "Home" });
    expect(slider.getAttribute("aria-valuetext")).toContain("2026-01-01: Tokens 10.00");
    expect(container.querySelector(".chart-tooltip-date")?.textContent).toBe("2026-01-01");
  });
  it("breaks the line across missing reporting periods instead of drawing false continuity", () => {
    const { container } = render(<CriHistoryChart history={[point("2026-01-01"), point("2026-01-02", 11), point("2026-01-10", 12), point("2026-01-11", 13)]} series={[{ key: "value", label: "Tokens", color: "green", axis: "left" }]} maxGapMs={2 * 86_400_000} title="Usage" />);
    expect(container.querySelectorAll('path[stroke="green"]')).toHaveLength(2);
  });
  it("retains quarterly bars without adding a second series", () => {
    const { container } = render(<CriHistoryChart history={[point("2026-03-31", 10), point("2026-06-30", 20)]} series={[{ key: "value", label: "Revenue", color: "green", axis: "left", renderAs: "bar" }]} title="Revenue" />);
    const bars = [...container.querySelectorAll(".history-bar")];
    expect(bars).toHaveLength(2);
    expect(Number(bars[1].getAttribute("height"))).toBeCloseTo(Number(bars[0].getAttribute("height")) * 2);
    expect(container.querySelectorAll(".chart-legend-item")).toHaveLength(1);
  });
  it("preserves existing dual-series legends", () => {
    const { container } = render(<CriHistoryChart history={[{ date: "2026-01-01", a: 1, b: 9 }, { date: "2026-01-02", a: 2, b: 8 }]} series={[{ key: "a", label: "SPX", color: "green", axis: "left" }, { key: "b", label: "Ratio", color: "gray", axis: "right" }]} title="Existing comparison" />);
    expect(container.querySelectorAll(".chart-legend-item")).toHaveLength(2);
  });
  it("inspects nullable comparison observations without formatting missing values", () => {
    render(<CriHistoryChart history={[{ date: "2026-01-01", a: 1, b: null }, { date: "2026-01-02", a: 2, b: 8 }]} series={[{ key: "a", label: "SPX", color: "green", axis: "left" }, { key: "b", label: "Ratio", color: "gray", axis: "right", format: value => value.toFixed(2) }]} title="Missing comparison" />);
    const slider = screen.getByRole("slider", { name: "Inspect Missing comparison history" });
    fireEvent.keyDown(slider, { key: "Home" });
    expect(slider.getAttribute("aria-valuetext")).toContain("Ratio ---");
  });
  it("makes shared brush handles operable by keyboard without crossing", () => {
    const change = vi.fn();
    render(<BrushMinimap values={[1, 2, 3, 4]} range={[1, 3]} onRangeChange={change} formatIndex={i => `Day ${i + 1}`} />);
    const start = screen.getByRole("slider", { name: "Start of visible range" });
    expect(start.getAttribute("aria-valuetext")).toBe("Day 2");
    fireEvent.keyDown(start, { key: "End" });
    expect(change).toHaveBeenLastCalledWith([2, 3]);
    fireEvent.keyDown(screen.getByRole("slider", { name: "End of visible range" }), { key: "Home" });
    expect(change).toHaveBeenLastCalledWith([1, 2]);
  });
});
