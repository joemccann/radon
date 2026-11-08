/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { formatTrades, MISSING_IMBALANCE, type ImbalanceData } from "@/lib/imbalance";
const hook = vi.hoisted(() => vi.fn());
vi.mock("@/lib/useImbalance", () => ({ useImbalance: hook }));
vi.mock("../components/CriHistoryChart", () => ({
  default: ({ history, title }: { history: unknown[]; title: string }) =>
    <div data-testid="pane" data-history={JSON.stringify(history)}>{title}</div>,
}));
vi.mock("../components/FreshnessRail", () => ({ default: () => <div /> }));
vi.mock("../components/BrushMinimap", () => ({
  default: () => <div data-testid="imbalance-brush" />,
}));
vi.mock("../components/InfoTooltip", () => ({
  default: ({ text }: { text: string }) => <span data-testid="imbalance-tooltip">{text}</span>,
}));
import Panel from "../components/ImbalancePanel";

const point = {
  date: "2026-11-06", ask: 13_124_065, bid: 12_215_551, batd: 908_514,
  imbalance: 908_514, ema10: 200_000, spx_close: 7376.86,
};
const data: ImbalanceData = {
  scan_time: "2026-11-07T00:00:00Z", data_date: point.date, current: point, series: [point],
  source: {
    provider: "tradingview", units: "trade_count", units_verified: true,
    history_complete: false, source_finality: "unverified", universe_verified: false,
  },
};

beforeEach(() => hook.mockReturnValue({ data, loading: false, syncing: false, error: null, syncNow: vi.fn() }));
afterEach(cleanup);

describe("ASK-BID trade-count imbalance", () => {
  it("keeps absent readings unavailable and signs trade counts", () => {
    expect(formatTrades(null)).toBe("---");
    expect(formatTrades(NaN)).toBe("---");
    expect(formatTrades(0)).toBe("0");
    expect(formatTrades(908_514)).toBe("+908,514");
    expect(formatTrades(-30)).toBe("-30");
  });

  it("renders signed bars, SPX pane, trade-count copy, and tooltip", () => {
    render(<Panel />);
    expect(screen.getAllByTestId("pane")).toHaveLength(2);
    expect(screen.getByTestId("imbalance-net").textContent).toBe("+908,514");
    expect(screen.getByTestId("imbalance-spx").textContent).toBe("7376.86");
    expect(screen.getByTestId("imbalance-source").textContent).toContain("not share volume");
    expect(screen.getByTestId("imbalance-source").textContent).toContain("Universe, session coverage, and tick-rule classification are unverified");
    expect(screen.getByTestId("imbalance-tooltip").textContent).toContain("NUMBER OF TRADES");
  });

  it("preserves null gaps in both panes", () => {
    hook.mockReturnValue({
      data: {
        ...data,
        series: [point, { ...point, date: "2026-11-09", ask: null, bid: null, imbalance: null, ema10: null, spx_close: null }],
      },
      loading: false,
    });
    render(<Panel />);
    const rows = JSON.parse(screen.getAllByTestId("pane")[0].getAttribute("data-history")!);
    expect(rows[1]).toMatchObject({ imbalance: null, positive: null, negative: null, spx_close: null });
  });

  it("shows the measurement condition when no aligned session exists", () => {
    hook.mockReturnValue({ data: MISSING_IMBALANCE, loading: false });
    render(<Panel />);
    expect(screen.getByText("No aligned ASK-BID trade-count session yet")).toBeTruthy();
  });

  it("applies one range to both panes", () => {
    const series = Array.from({ length: 100 }, (_, i) => ({
      ...point,
      date: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10),
    }));
    hook.mockReturnValue({ data: { ...data, series }, loading: false });
    render(<Panel />);
    fireEvent.click(screen.getByRole("button", { name: "1M", exact: true }));
    const lengths = screen.getAllByTestId("pane").map((p) => JSON.parse(p.getAttribute("data-history")!).length);
    expect(lengths).toEqual([21, 21]);
  });
});
