/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import CriHistoryChart, { type ChartSeries } from "../components/CriHistoryChart";

const rows = [{ date: "2026-10-19", value: 0.2, other: 100 }, { date: "2026-10-20", value: 0.8, other: 200 }, { date: "2026-10-21", value: null, other: 300 }];
type Row = typeof rows[number];
const base: ChartSeries<Row> = { key: "value", label: "Value", axis: "left", color: "var(--positive)" };
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 800, height: 440, x: 0, y: 0, top: 0, bottom: 440, left: 0, right: 800, toJSON() {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
describe("CriHistoryChart rendering", () => {
  it("preserves connected line rendering and observation markers", () => {
    const { container } = render(<CriHistoryChart history={rows} series={[base]} title="Line" />);
    expect(container.querySelectorAll("path[stroke='var(--positive)']")).toHaveLength(1);
    expect(container.querySelectorAll("circle.dot-value")).toHaveLength(2);
    expect(container.querySelectorAll(".history-bar")).toHaveLength(0);
  });
  it("preserves zero-based bars and signed heights", () => {
    const { container } = render(<CriHistoryChart history={[...rows.slice(0, 2), { ...rows[2], value: -0.2 }]} series={[{ ...base, renderAs: "bar" }]} title="Bars" />);
    expect(container.querySelectorAll(".history-bar")).toHaveLength(3);
    expect(container.querySelectorAll("path[stroke='var(--positive)']")).toHaveLength(0);
    for (const rect of container.querySelectorAll(".history-bar")) expect(Number(rect.getAttribute("height"))).toBeGreaterThan(0);
  });
  it("renders points without connecting a single valid SAR observation", () => {
    const { container } = render(<CriHistoryChart history={[{ ...rows[0], value: null }, rows[1], rows[2]]} series={[{ ...base, renderAs: "points" }]} title="SAR" />);
    expect(container.querySelectorAll("circle.dot-value")).toHaveLength(1);
    expect(container.querySelectorAll("path[stroke='var(--positive)']")).toHaveLength(0);
  });
  it("pins a bounded indicator to [0,1]", () => {
    const { container } = render(<CriHistoryChart history={rows} series={[{ ...base, renderAs: "points", yDomain: [0, 1] }]} title="Bounded" />);
    const dots = [...container.querySelectorAll("circle.dot-value")];
    expect(Number(dots[0].getAttribute("cy"))).toBeCloseTo(376 * 0.8);
    expect(Number(dots[1].getAttribute("cy"))).toBeCloseTo(376 * 0.2);
  });
  it.each([[NaN, 1], [0, Infinity], [1, 0], [1, 1]])("rejects invalid domain %s..%s and falls back to finite data", (low, high) => {
    const { container } = render(<CriHistoryChart history={rows} series={[{ ...base, renderAs: "points", yDomain: [low, high] }]} title="Invalid bounds" />);
    const dots = [...container.querySelectorAll("circle.dot-value")];
    expect(dots).toHaveLength(2);
    expect(Number(dots[0].getAttribute("cy"))).toBeGreaterThan(Number(dots[1].getAttribute("cy")));
    for (const dot of dots) expect(Number.isFinite(Number(dot.getAttribute("cy")))).toBe(true);
  });
  it("excludes nonfinite points on the shared cumulative/SAR axis", () => {
    const { container } = render(<CriHistoryChart history={[{ ...rows[0], value: NaN }, { ...rows[1], other: Infinity }, rows[2]]} sharedAxis series={[base, { key: "other", label: "SAR", axis: "right", color: "var(--text-secondary)", renderAs: "points" }]} title="Shared" />);
    expect(container.querySelectorAll("circle.dot-other")).toHaveLength(2);
    for (const circle of container.querySelectorAll("circle")) expect(Number.isFinite(Number(circle.getAttribute("cy")))).toBe(true);
  });
});
