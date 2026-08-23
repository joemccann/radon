/**
 * @vitest-environment jsdom
 *
 * BreadthPanel — NYSE advance/decline market-breadth regime tab.
 *
 * Pins the gating contract (SpectralLoader while sampling, SectionEmptyState
 * on the settled missing case), the desktop summary tiles, both chart titles,
 * and the divergence chip text/tone for each of none/bearish/bullish.
 */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { BreadthData, BreadthHistoryEntry, BreadthIntradayPoint } from "@/lib/useBreadth";

// jsdom doesn't ship ResizeObserver; CriHistoryChart wires one up on mount.
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

const mockUseBreadth = vi.fn();
vi.mock("@/lib/useBreadth", () => ({
  useBreadth: (...args: unknown[]) => mockUseBreadth(...args),
}));

import BreadthPanel, { pearsonCorrelation } from "../components/BreadthPanel";

afterEach(() => {
  cleanup();
  mockUseBreadth.mockReset();
});

function buildHistory(count: number): BreadthHistoryEntry[] {
  return Array.from({ length: count }, (_, i) => ({
    date: `2026-06-${String((i % 28) + 1).padStart(2, "0")}`,
    net_ad: i % 2 === 0 ? 400 + i : -(200 + i),
    cum_ad: 48000 + i * 90,
    spy_close: 600 + i * 0.5,
  }));
}

function buildIntraday(): BreadthIntradayPoint[] {
  return [
    { time: "2026-07-01T13:35:00Z", net_ad: 120 },
    { time: "2026-07-01T13:40:00Z", net_ad: -60 },
    { time: "2026-07-01T13:45:00Z", net_ad: 240 },
    { time: "2026-07-01T13:50:00Z", net_ad: 310 },
  ];
}

function buildBreadthData(overrides: Partial<BreadthData> = {}): BreadthData {
  return {
    scan_time: "2026-07-01T19:55:00Z",
    market_open: true,
    source: "ib",
    latest: {
      session_date: "2026-07-01",
      net_ad: 820,
      net_ad_prev: -140,
      tick: 250,
      cum_ad: 51230,
      cum_ad_change_20d: 1900,
      spy_change_20d_pct: 2.4,
      sessions_positive_20: 13,
      divergence: "none",
    },
    history: buildHistory(30),
    intraday: buildIntraday(),
    ...overrides,
  };
}

type HookState = ReturnType<typeof hookState>;

function hookState(partial: Partial<{
  data: BreadthData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
}> = {}) {
  return {
    data: null as BreadthData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: HookState) {
  mockUseBreadth.mockReturnValue(state);
  return render(<BreadthPanel />);
}

describe("BreadthPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Sampling NYSE advance/decline breadth")).toBeTruthy();
  });

  it("shows the SpectralLoader while an explicit sync runs with no data", () => {
    renderPanel(hookState({ syncing: true }));
    expect(screen.getByText("Sampling NYSE advance/decline breadth")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the settled missing case", () => {
    renderPanel(
      hookState({
        data: {
          missing: true,
          scan_time: null,
          latest: null,
          history: [],
          intraday: [],
        } as unknown as BreadthData,
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No breadth data yet")).toBeTruthy();
  });
});

describe("BreadthPanel — content", () => {
  it("renders the summary tiles", () => {
    renderPanel(hookState({ data: buildBreadthData() }));
    expect(screen.getByText("NET A/D")).toBeTruthy();
    expect(screen.getByText("TICK")).toBeTruthy();
    expect(screen.getByText("NET BREADTH 20D")).toBeTruthy();
    expect(screen.getByText("SESSIONS POSITIVE")).toBeTruthy();
    expect(screen.getByText("DIVERGENCE")).toBeTruthy();
    expect(screen.getByText("13/20")).toBeTruthy();
  });

  it("renders both chart titles", () => {
    renderPanel(hookState({ data: buildBreadthData() }));
    expect(screen.getByText("CUMULATIVE A/D LINE VS SPY")).toBeTruthy();
    expect(screen.getByText("SESSION NET A/D")).toBeTruthy();
  });

  it("shows the muted no-session state when intraday is empty", () => {
    renderPanel(hookState({ data: buildBreadthData({ intraday: [] }) }));
    expect(screen.getByText("NO SESSION DATA")).toBeTruthy();
  });
});

describe("pearsonCorrelation", () => {
  it("returns +1 for perfectly positively correlated series", () => {
    expect(pearsonCorrelation([1, 2, 3, 4], [10, 20, 30, 40])).toBeCloseTo(1, 8);
  });

  it("returns -1 for perfectly negatively correlated series", () => {
    expect(pearsonCorrelation([1, 2, 3, 4], [40, 30, 20, 10])).toBeCloseTo(-1, 8);
  });

  it("skips pairs where either value is null", () => {
    expect(pearsonCorrelation([1, 2, null, 3, 4], [10, 20, 999, null, 40])).toBeCloseTo(1, 8);
  });

  it("returns null with fewer than 3 valid pairs", () => {
    expect(pearsonCorrelation([1, 2], [10, 20])).toBeNull();
    expect(pearsonCorrelation([1, 2, null, 4], [10, null, 30, 40])).toBeNull();
  });

  it("returns null when a series has zero variance", () => {
    expect(pearsonCorrelation([5, 5, 5, 5], [10, 20, 30, 40])).toBeNull();
  });
});

describe("BreadthPanel — A/D vs SPY correlation stat", () => {
  /** First 9 sessions anti-correlate, last 21 correlate perfectly — so the
   *  default 1M window reads +1.00 and the All window reads something else. */
  function buildSplitHistory(): BreadthHistoryEntry[] {
    return Array.from({ length: 30 }, (_, i) => ({
      date: `2026-06-${String(i + 1).padStart(2, "0")}`,
      net_ad: 100,
      cum_ad: i < 9 ? 50000 - i * 200 : 40000 + i * 90,
      spy_close: 600 + i * 0.5,
    }));
  }

  it("renders the Pearson correlation of the visible window", () => {
    renderPanel(hookState({ data: buildBreadthData({ history: buildSplitHistory() }) }));
    expect(screen.getByTestId("breadth-corr-stat").textContent).toBe("ρ +1.00");
  });

  it("recomputes when the range preset changes", () => {
    const history = buildSplitHistory();
    renderPanel(hookState({ data: buildBreadthData({ history }) }));
    fireEvent.click(screen.getByTestId("breadth-history-range-chips-all"));
    const expected = pearsonCorrelation(
      history.map((h) => h.cum_ad),
      history.map((h) => h.spy_close),
    );
    expect(expected).not.toBeNull();
    expect(screen.getByTestId("breadth-corr-stat").textContent).toBe(
      `ρ ${expected! >= 0 ? "+" : ""}${expected!.toFixed(2)}`,
    );
  });

  it("renders --- with fewer than 3 valid pairs", () => {
    const history: BreadthHistoryEntry[] = [
      { date: "2026-06-01", net_ad: 100, cum_ad: 100, spy_close: 600 },
      { date: "2026-06-02", net_ad: 100, cum_ad: 200, spy_close: null },
      { date: "2026-06-03", net_ad: 100, cum_ad: 300, spy_close: null },
      { date: "2026-06-04", net_ad: 100, cum_ad: 400, spy_close: 602 },
    ];
    renderPanel(hookState({ data: buildBreadthData({ history }) }));
    expect(screen.getByTestId("breadth-corr-stat").textContent).toBe("ρ ---");
  });
});

describe("BreadthPanel — divergence chip", () => {
  function chipFor(divergence: "none" | "bearish" | "bullish"): HTMLElement {
    const data = buildBreadthData();
    data.latest = { ...data.latest!, divergence };
    renderPanel(hookState({ data }));
    return screen.getByTestId("breadth-divergence-chip");
  }

  it("renders NONE in muted tone", () => {
    const chip = chipFor("none");
    expect(chip.textContent).toBe("NONE");
    expect(chip.style.color).toBe("var(--text-muted)");
  });

  it("renders BEARISH in the negative tone", () => {
    const chip = chipFor("bearish");
    expect(chip.textContent).toBe("BEARISH");
    expect(chip.style.color).toBe("var(--negative)");
  });

  it("renders BULLISH in the positive tone", () => {
    const chip = chipFor("bullish");
    expect(chip.textContent).toBe("BULLISH");
    expect(chip.style.color).toBe("var(--positive)");
  });
});
