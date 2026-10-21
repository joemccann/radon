/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import HighLowLinePanel from "../components/HighLowLinePanel";

const mocks = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock("@/lib/useHighLowLine", () => ({ useHighLowLine: () => mocks.state }));
vi.mock("@/lib/useViewport", () => ({ useViewport: () => ({ isMobile: false, width: 1440 }) }));

function data() {
  const series = Array.from({ length: 40 }, (_, i) => ({ date: new Date(Date.UTC(2026, 7, 3+i)).toISOString().slice(0,10), period_start: "2026-08-03", period_end: "2026-08-07", period_complete: true,
    highs: 30+i, lows: 15, net: 15+i, cumulative: 1000+i*10, rsi14: i<14 ? null : 45, stoch_rsi14: i<27 ? null : 0.1,
    sar: i===0 ? null : 1100+i*10, sar_direction: i===0 ? null : "falling", reset_to_zero: false, source_fractional: false }));
  return { missing: false, degraded: false, scan_time: new Date().toISOString(), source_last_modified: null, data_date: series.at(-1)!.date, count: series.length,
    weekly_count: series.length, series, weekly_series: series, current: { ...series.at(-1), zone: "oversold" }, weekly_current: { ...series.at(-1), zone: "oversold", period_complete: false },
    source_hash: "a".repeat(64), source: { name: "StockCharts DecisionPoint", url: "https://stockcharts.com", symbols: { highs: "!NEWHINYC", lows: "!NEWLONYC" }, history_start: "2003-01-02", history_end: series.at(-1)!.date, fractional_policy: "preserved", fractional_observations: 275, excluded_non_sessions: [] },
    baseline: { date: "2003-01-02", value: 0, convention: "before_first_observation" }, settings: { rsi_period: 14, stoch_period: 14, smoothing: "Wilder", sar_start: 1, sar_increment: 1, sar_max: 1, daily_ohlc: "close_only", weekly_ohlc: "daily_cumulative_range", default_interval: "weekly" } };
}
beforeEach(() => {
  cleanup(); vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  mocks.state = { data: data(), loading: false, syncing: false, error: null, lastSync: new Date().toISOString(), syncNow: vi.fn() };
});
describe("High-Low Line panel", () => {
  it("shows an honest loading label", () => {
    mocks.state = { ...mocks.state, data: null, loading: true }; render(<HighLowLinePanel />);
    expect(screen.getByText(/Loading.*high.low/i)).toBeTruthy();
  });
  it("describes the missing measurement", () => {
    mocks.state = { ...mocks.state, data: { missing: true, series: [], weekly_series: [] } }; render(<HighLowLinePanel />);
    expect(screen.getByText(/Awaiting.*common.stock/i)).toBeTruthy();
  });
  it("renders both charts, actual source period and mandatory countdown", () => {
    render(<HighLowLinePanel />);
    expect(screen.getByTestId("high-low-line-cumulative-chart")).toBeTruthy();
    expect(screen.getByTestId("high-low-line-stoch-chart")).toBeTruthy();
    expect(screen.getByTestId("high-low-line-freshness-rail")).toBeTruthy();
    expect(screen.getByTestId("high-low-line-strip-asof").textContent).toContain(data().data_date);
    expect(screen.getByText(/Week in progress/i)).toBeTruthy();
  });
  it("switches calculation interval without restarting indicator history", () => {
    render(<HighLowLinePanel />); fireEvent.click(screen.getByRole("button", { name: /^Daily$/i }));
    expect(screen.getByRole("button", { name: /^Daily$/i }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText(/Week in progress/i)).toBeNull();
  });
  it("never generates nonfinite SVG coordinates", () => {
    const broken = data(); broken.series[30].stoch_rsi14 = Number.NaN; broken.weekly_series = broken.series;
    mocks.state = { ...mocks.state, data: broken }; const { container } = render(<HighLowLinePanel />);
    for (const el of container.querySelectorAll("path")) expect(el.getAttribute("d") ?? "").not.toMatch(/NaN|Infinity/);
  });
});

it("uses distinct weekly payload values and switches to the daily calculation", () => {
  const payload = data();
  payload.weekly_current = { ...payload.weekly_current, cumulative: 4321.1234, net: -40, stoch_rsi14: 0.9 };
  payload.current = { ...payload.current, cumulative: 1390, net: 40, stoch_rsi14: 0.00001 };
  mocks.state = { ...mocks.state, data: payload };
  render(<HighLowLinePanel />);
  expect(screen.getByTestId("high-low-line-strip-cumulative").textContent).toContain("4,321.1234");
  expect(screen.getByTestId("high-low-line-strip-stoch").textContent).toContain("OVERBOUGHT");
  fireEvent.click(screen.getByRole("button", { name: "Daily" }));
  expect(screen.getByTestId("high-low-line-strip-cumulative").textContent).toContain("1,390");
  expect(screen.getByTestId("high-low-line-strip-net").textContent).toContain("+40");
  expect(screen.getByTestId("high-low-line-strip-stoch").textContent).not.toContain("RESET");
});
it("defaults to a calendar five-year window and shares brush ranges between both charts", () => {
  const payload = data();
  payload.weekly_series = ["2003-01-02", "2020-01-02", "2022-01-02", "2024-01-02", "2026-01-02"].map((date, i) => ({ ...payload.series[i], date }));
  mocks.state = { ...mocks.state, data: payload };
  render(<HighLowLinePanel />);
  expect(screen.getByTestId("high-low-line-range-chips-5y").getAttribute("aria-pressed")).toBe("true");
  const charts = screen.getAllByRole("slider", { name: /Inspect .* history/ });
  expect(charts.map(chart => chart.getAttribute("aria-valuemax"))).toEqual(["2", "2"]);
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  expect(charts.map(chart => chart.getAttribute("aria-valuemax"))).toEqual(["4", "4"]);
  fireEvent.keyDown(screen.getByRole("slider", { name: "Start of visible range" }), { key: "ArrowRight" });
  expect(screen.getByTestId("high-low-line-range-chips-custom")).toBeTruthy();
  expect(charts.map(chart => chart.getAttribute("aria-valuemax"))).toEqual(["3", "3"]);
  expect(screen.getByTestId("high-low-line-strip-cumulative").textContent).toContain("1,390");
});
it("keeps source/baseline/fraction/SAR and UTC sample notes visible", () => {
  render(<HighLowLinePanel />);
  const notes = screen.getByTestId("high-low-line-notes").textContent ?? "";
  for (const text of ["!NEWHINYC", "!NEWLONYC", "2003-01-02", "fractional", "range of daily cumulative closes", "1.0", "00:30 UTC"]) expect(notes).toContain(text);
  expect(screen.getByTestId("high-low-line-sample").textContent).toMatch(/Sampled .* UTC.*Weekly calculation as of/);
});
it("shows lag independently of the sample clock", () => {
  mocks.state = { ...mocks.state, data: { ...data(), degraded: true, expected_session_date: "2026-10-20" } };
  render(<HighLowLinePanel />);
  expect(screen.getByTestId("high-low-line-degraded").textContent).toContain("2026-10-20");
  expect(screen.getByTestId("high-low-line-sample").textContent).toContain("UTC");
});
it("keeps absent weekly calculations unavailable rather than borrowing daily values", () => {
  mocks.state = { ...mocks.state, data: { ...data(), weekly_current: null, weekly_series: [] } };
  render(<HighLowLinePanel />);
  expect(screen.getByTestId("high-low-line-strip-cumulative").textContent).toContain("---");
  expect(screen.getByTestId("high-low-line-strip-stoch").textContent).toContain("UNAVAILABLE");
  expect(screen.getAllByText("NO HISTORY AVAILABLE")).toHaveLength(2);
});
it("handles a missing or invalid sample time independently of source observations", () => {
  mocks.state = { ...mocks.state, data: { ...data(), scan_time: "invalid", stale: true } };
  render(<HighLowLinePanel />);
  expect(screen.getByTestId("high-low-line-sample").textContent).toContain("Sampled ---");
  expect(screen.getByTestId("high-low-line-degraded").textContent).toContain("Writer sample is stale");
  expect(screen.getByTestId("high-low-line-strip-asof").textContent).toContain(data().data_date);
});
