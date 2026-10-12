/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { breadthValue, MISSING_RECORD_HIGH_BREADTH, type RecordHighBreadthData } from "@/lib/recordHighBreadth";
const hook = vi.hoisted(() => vi.fn());
vi.mock("@/lib/useRecordHighBreadth", () => ({ useRecordHighBreadth: hook }));
vi.mock("../components/CriHistoryChart", () => ({ default: ({ history, title }: { history: unknown[]; title: string }) => <div data-testid="pane" data-history={JSON.stringify(history)}>{title}</div> }));
vi.mock("../components/FreshnessRail", () => ({ default: () => <div /> }));
import Panel from "../components/RecordHighBreadthPanel";
const point = { date: "2026-09-18", spx_close: 7650.5, above_200_pct: 49.5, new_highs: 5, new_lows: 29, net_high_low_per_500: -4.8 };
const data: RecordHighBreadthData = { scan_time: "2026-09-19T00:00:00Z", data_date: point.date, current: point, series: [point], source: { provider: "tradingview", denominator: 500, denominator_verified: false, history_complete: false, source_finality: "unverified" } };
beforeEach(() => hook.mockReturnValue({ data, loading: false, syncing: false, error: null, syncNow: vi.fn() }));
afterEach(cleanup);
describe("S&P 500 breadth source variant", () => {
  it("keeps absent readings unavailable", () => { expect(breadthValue(null)).toBe("---"); expect(breadthValue(NaN)).toBe("---"); expect(breadthValue(0)).toBe("0.00"); });
  it("renders three panes and truthful normalization and history copy", () => {
    render(<Panel />);
    expect(screen.getAllByTestId("pane")).toHaveLength(3);
    expect(screen.getByTestId("record-high-breadth-above").textContent).toBe("49.50%");
    expect(screen.getByTestId("record-high-breadth-net").textContent).toBe("-4.80%");
    expect(screen.getByTestId("record-high-breadth-source").textContent).toContain("actual issue denominator is unverified");
    expect(screen.getByTestId("record-high-breadth-source").textContent).toContain("1973 and 1999 are unverified");
  });
  it("flags the unverified net figure as an estimate instead of a prose caveat", () => {
    render(<Panel />);
    const net = screen.getByTestId("record-high-breadth-strip-net");
    expect(net.querySelector('[data-testid="record-high-breadth-net-flag"]')?.textContent).toBe("EST");
    expect(screen.getByTestId("record-high-breadth-source").textContent).toMatch(/^EST /);
    expect(screen.queryByTestId("record-high-breadth-strip-date")).toBeNull();
    cleanup();
    hook.mockReturnValue({ data: { ...data, source: { ...data.source!, denominator_verified: true } }, loading: false });
    render(<Panel />);
    expect(screen.queryByTestId("record-high-breadth-net-flag")).toBeNull();
  });
  it("preserves null gaps in all panes", () => {
    hook.mockReturnValue({ data: { ...data, series: [point, { ...point, date: "2026-09-21", spx_close: null, above_200_pct: null, net_high_low_per_500: null }] }, loading: false });
    render(<Panel />);
    const rows = JSON.parse(screen.getAllByTestId("pane")[2].getAttribute("data-history")!);
    expect(rows[1]).toMatchObject({ spx_close: null, above_200_pct: null, positive: null, negative: null });
  });
  it("shows the measurement condition when no aligned session exists", () => {
    hook.mockReturnValue({ data: MISSING_RECORD_HIGH_BREADTH, loading: false }); render(<Panel />);
    expect(screen.getByText("No aligned S&P 500 breadth session yet")).toBeTruthy();
  });
  it("applies one range to all panes", () => {
    const series = Array.from({ length: 100 }, (_, i) => ({ ...point, date: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10) }));
    hook.mockReturnValue({ data: { ...data, series }, loading: false }); render(<Panel />);
    fireEvent.click(screen.getByRole("button", { name: "1M", exact: true }));
    const lengths = screen.getAllByTestId("pane").map(p => JSON.parse(p.getAttribute("data-history")!).length);
    expect(lengths).toEqual([21, 21, 21]);
  });
});
