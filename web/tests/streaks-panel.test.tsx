/** @vitest-environment jsdom */
/**
 * STREAKS regime tab — pure lib helpers + panel gates, strip, ticker form,
 * and the two-pane chart (log price line over streak bars) with a NaN guard.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import {
  formatCloseValue,
  formatDayChangePct,
  formatRunsAtOrAbove,
  formatStreakDays,
  formatUpDayPct,
  sourceLabel,
  streakTone,
  type StreaksData,
} from "../lib/streaks";

/* ─── Pure lib helpers ─────────────────────────────────── */

describe("streaks lib formatters", () => {
  it("formats streak day counts with singular/plural", () => {
    expect(formatStreakDays(0)).toBe("0 DAYS");
    expect(formatStreakDays(1)).toBe("1 DAY");
    expect(formatStreakDays(3)).toBe("3 DAYS");
  });

  it("tones a live streak positive and a broken streak muted", () => {
    expect(streakTone(3)).toBe("pos");
    expect(streakTone(0)).toBe("mut");
  });

  it("formats day change with sign and two decimals", () => {
    expect(formatDayChangePct(0.81)).toBe("+0.81%");
    expect(formatDayChangePct(-0.5)).toBe("-0.50%");
    expect(formatDayChangePct(null)).toBe("---");
  });

  it("formats closes with grouping and two decimals", () => {
    expect(formatCloseValue(769.3499755859375)).toBe("769.35");
    expect(formatCloseValue(1234.5)).toBe("1,234.50");
    expect(formatCloseValue(null)).toBe("---");
  });

  it("formats the precedent cell from runs_ge_current", () => {
    expect(formatRunsAtOrAbove(38)).toBe("38 RUNS");
    expect(formatRunsAtOrAbove(null)).toBe("---");
  });

  it("formats the up-day share", () => {
    expect(formatUpDayPct(53.4)).toBe("53.4%");
    expect(formatUpDayPct(null)).toBe("---");
  });

  it("labels every source honestly", () => {
    expect(sourceLabel("ib")).toBe("IB");
    expect(sourceLabel("uw")).toBe("UNUSUAL WHALES");
    expect(sourceLabel("rh")).toBe("ROBINHOOD");
    expect(sourceLabel("robinhood")).toBe("ROBINHOOD");
    expect(sourceLabel("yahoo")).toBe("YAHOO");
    expect(sourceLabel("cache")).toBe("CACHED");
    expect(sourceLabel(null)).toBe("---");
  });
});

/* ─── Panel fixtures + mocks ───────────────────────────── */

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    class StubResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
      StubResizeObserver;
  }
});

const mockUseStreaks = vi.fn();
vi.mock("@/lib/useStreaks", () => ({
  useStreaks: (...args: unknown[]) => mockUseStreaks(...args),
}));

const replaceSpy = vi.fn();
let mockedSearch = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: replaceSpy,
    push: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/regime/streaks",
  useSearchParams: () => new URLSearchParams(mockedSearch),
}));

import StreaksPanel from "../components/StreaksPanel";

/** n sessions; closes repeat +1, +1, -0.5 so streaks cycle 0,1,2,0. */
function buildSeries(n: number) {
  const series: Array<{ date: string; close: number; streak: number }> = [];
  let close = 100;
  let streak = 0;
  for (let i = 0; i < n; i += 1) {
    if (i > 0) {
      const up = i % 4 === 1 || i % 4 === 2;
      close = up ? close + 1 : close - 0.5;
      streak = up ? streak + 1 : 0;
    }
    const day = String((i % 27) + 1).padStart(2, "0");
    const month = i < 27 ? "01" : "02";
    series.push({ date: `2026-${month}-${day}`, close, streak });
  }
  return series;
}

function buildData(overrides: Partial<StreaksData> = {}): StreaksData {
  const series = buildSeries(32);
  const last = series[series.length - 1];
  return {
    symbol: "SPY",
    scan_time: "2026-08-30T21:00:00+00:00",
    source: "uw",
    missing: false,
    count: series.length,
    first_date: series[0].date,
    last_date: last.date,
    current: {
      date: last.date,
      close: last.close,
      streak: last.streak,
      day_change_pct: 0.81,
    },
    stats: {
      max_streak: 12,
      max_streak_end: "2017-10-05",
      runs_total: 620,
      runs_ge_current: 38,
      avg_run: 1.9,
      up_day_pct: 53.4,
    },
    series,
    ...overrides,
  };
}

function hookState(partial: Record<string, unknown> = {}) {
  return {
    data: null,
    loading: false,
    error: null,
    refresh: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseStreaks.mockReturnValue(state);
  return render(<StreaksPanel />);
}

beforeEach(() => {
  mockUseStreaks.mockReset();
  replaceSpy.mockReset();
  mockedSearch = "";
});

afterEach(() => {
  cleanup();
});

/* ─── Gates ────────────────────────────────────────────── */

describe("StreaksPanel gates", () => {
  it("shows the loader while the first fetch is in flight", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading daily close series")).toBeTruthy();
    // The ticker form stays reachable in every state.
    expect(screen.getByTestId("streaks-symbol-input")).toBeTruthy();
  });

  it("shows the unreachable state when the route errored", () => {
    renderPanel(hookState({ error: "Failed to fetch streaks" }));
    expect(screen.getByText("Streak feed unreachable")).toBeTruthy();
  });

  it("shows the missing state naming the symbol", () => {
    renderPanel(
      hookState({
        data: buildData({
          missing: true,
          count: 0,
          current: null,
          stats: null,
          series: [],
        }),
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No daily history for SPY")).toBeTruthy();
  });
});

/* ─── Strip ────────────────────────────────────────────── */

describe("StreaksPanel strip", () => {
  it("renders current streak, record, precedent, and last session cells", () => {
    const data = buildData({
      current: { date: "2026-08-28", close: 769.35, streak: 3, day_change_pct: 0.81 },
      last_date: "2026-08-28",
    });
    renderPanel(hookState({ data }));

    expect(screen.getByTestId("streaks-strip-current").textContent).toContain("3 DAYS");
    const record = screen.getByTestId("streaks-strip-record").textContent ?? "";
    expect(record).toContain("12");
    expect(record).toContain("LAST HIT 2017-10-05");
    expect(screen.getByTestId("streaks-strip-precedent").textContent).toContain("38 RUNS");
    expect(screen.getByTestId("streaks-strip-updays").textContent).toContain("53.4%");
    const last = screen.getByTestId("streaks-strip-last").textContent ?? "";
    expect(last).toContain("769.35");
    expect(last).toContain("2026-08-28 · UNUSUAL WHALES");
  });
});

/* ─── Chart ────────────────────────────────────────────── */

describe("StreaksPanel chart", () => {
  it("renders the two-pane chart: a stroked price path and one bar per up session", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));

    expect(screen.getByTestId("streaks-chart-section")).toBeTruthy();
    const svg = container.querySelector("svg[data-testid='streaks-chart']");
    expect(svg).toBeTruthy();
    const paths = svg!.querySelectorAll("path");
    expect(paths.length).toBeGreaterThanOrEqual(1);
    // 32 sessions cycling 0,1,2,0 -> 16 sessions carry a positive streak.
    expect(svg!.querySelectorAll("rect.streaks-bar")).toHaveLength(16);
    expect(screen.getByText("SPY DAILY CLOSE VS CONSECUTIVE DAILY GAINS")).toBeTruthy();
  });

  it("never emits NaN into any path", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    for (const path of container.querySelectorAll("path")) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });

  it("offers range chips scaled to the available history", () => {
    renderPanel(hookState({ data: buildData() }));
    const chips = screen.getByTestId("streaks-range-chips");
    // 32 sessions: only the 1M preset fits, plus All.
    expect(chips.textContent).toContain("1M");
    expect(chips.textContent).toContain("All");
    expect(chips.textContent).not.toContain("1Y");
  });
});

/* ─── Ticker form ──────────────────────────────────────── */

describe("StreaksPanel ticker form", () => {
  it("defaults to SPY and honors ?symbol= deep links", () => {
    mockedSearch = "symbol=iwm";
    renderPanel(hookState({ data: buildData({ symbol: "IWM" }) }));
    expect(mockUseStreaks).toHaveBeenCalledWith("IWM");
    const input = screen.getByTestId("streaks-symbol-input") as HTMLInputElement;
    expect(input.value).toBe("IWM");
  });

  it("submitting a new symbol refetches and mirrors it to the URL", () => {
    renderPanel(hookState({ data: buildData() }));
    const input = screen.getByTestId("streaks-symbol-input");
    fireEvent.change(input, { target: { value: "qqq" } });
    fireEvent.submit(screen.getByTestId("streaks-form"));

    expect(mockUseStreaks).toHaveBeenLastCalledWith("QQQ");
    expect(replaceSpy).toHaveBeenCalledWith("/regime/streaks?symbol=QQQ", { scroll: false });
  });

  it("rejects an invalid symbol inline without refetching", () => {
    renderPanel(hookState({ data: buildData() }));
    const input = screen.getByTestId("streaks-symbol-input");
    fireEvent.change(input, { target: { value: "BAD$" } });
    fireEvent.submit(screen.getByTestId("streaks-form"));

    expect(screen.getByText("Enter a valid ticker symbol.")).toBeTruthy();
    expect(mockUseStreaks).not.toHaveBeenCalledWith("BAD$");
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it("resubmitting the same symbol triggers a refresh", () => {
    const state = hookState({ data: buildData() });
    renderPanel(state);
    fireEvent.submit(screen.getByTestId("streaks-form"));
    expect(state.refresh).toHaveBeenCalledTimes(1);
  });
});
