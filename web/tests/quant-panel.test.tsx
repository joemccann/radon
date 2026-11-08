/**
 * @vitest-environment jsdom
 *
 * Quant sleeve — QUANT regime tab.
 *
 * Pure helpers (lib/quant.ts): signed percent / weight / dollar formatting,
 * equity-to-drawdown rows, range presets, share estimates, gross notional.
 *
 * QuantPanel: strategy chips, gating (SpectralLoader / SectionEmptyState),
 * the header strip, the chart title, the targets table, the orders table,
 * the freshness rail and the NaN guard.
 */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  QUANT_MISSING,
  QUANT_RANGE_PRESETS,
  QUANT_STRATEGIES,
  buildEquityRows,
  estimatedShares,
  formatPct,
  formatSignedPct,
  formatUsd,
  formatWeight,
  grossNotional,
  quantPresetRange,
  type QuantData,
} from "@/lib/quant";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatSignedPct / formatPct / formatWeight / formatUsd", () => {
  it("signs fractions as percents", () => {
    expect(formatSignedPct(0.059)).toBe("+5.9%");
    expect(formatSignedPct(-0.204)).toBe("-20.4%");
    expect(formatSignedPct(0)).toBe("0.0%");
    expect(formatSignedPct(null)).toBe("---");
    expect(formatSignedPct(Number.NaN)).toBe("---");
  });

  it("formats unsigned magnitudes", () => {
    expect(formatPct(0.082)).toBe("8.2%");
    expect(formatPct(undefined)).toBe("---");
  });

  it("formats weights to three decimals with a sign", () => {
    expect(formatWeight(0.25)).toBe("+0.250");
    expect(formatWeight(-0.75)).toBe("-0.750");
    expect(formatWeight(0)).toBe("0.000");
    expect(formatWeight(null)).toBe("---");
  });

  it("formats whole dollars with separators and a sign", () => {
    expect(formatUsd(25000)).toBe("$25,000");
    expect(formatUsd(-1234.56)).toBe("-$1,235");
    expect(formatUsd(null)).toBe("---");
  });
});

describe("buildEquityRows — running drawdown", () => {
  it("zips dates and equity and measures drawdown from the running peak", () => {
    const rows = buildEquityRows({
      dates: ["d1", "d2", "d3", "d4"],
      equity: [100, 120, 90, 130],
    });
    expect(rows.map((r) => r.date)).toEqual(["d1", "d2", "d3", "d4"]);
    expect(rows.map((r) => r.drawdown)).toEqual([0, 0, 90 / 120 - 1, 0]);
    expect(rows.every((r) => r.drawdown <= 0)).toBe(true);
  });

  it("drops non-positive or non-finite equity points and tolerates length drift", () => {
    const rows = buildEquityRows({ dates: ["d1", "d2", "d3"], equity: [100, Number.NaN] });
    expect(rows).toEqual([{ date: "d1", equity: 100, drawdown: 0 }]);
    expect(buildEquityRows(null)).toEqual([]);
  });
});

describe("quantPresetRange — multi-year presets", () => {
  it("exposes 1Y/3Y/5Y/All only", () => {
    expect(QUANT_RANGE_PRESETS.map((p) => p.slug)).toEqual(["1y", "3y", "5y", "all"]);
  });

  it("slices the trailing window", () => {
    expect(quantPresetRange("1y", 1000)).toEqual([748, 999]);
    expect(quantPresetRange("all", 1000)).toEqual([0, 999]);
    expect(quantPresetRange("5y", 300)).toEqual([0, 299]);
    expect(quantPresetRange("3y", 0)).toEqual([0, 0]);
  });
});

describe("estimatedShares / grossNotional", () => {
  it("floors whole shares and keeps the weight's sign", () => {
    expect(estimatedShares({ weight: 0.25, notional: 25000, close: 101.3 })).toBe(246);
    expect(estimatedShares({ weight: -0.25, notional: -25000, close: 101.3 })).toBe(-246);
    expect(estimatedShares({ weight: 0, notional: 0, close: 101.3 })).toBe(0);
  });

  it("returns null without a notional or a usable close", () => {
    expect(estimatedShares({ weight: 0.25, notional: null, close: 101.3 })).toBeNull();
    expect(estimatedShares({ weight: 0.25, notional: 25000, close: null })).toBeNull();
    expect(estimatedShares({ weight: 0.25, notional: 25000, close: 0 })).toBeNull();
  });

  it("sums absolute notionals, ignoring nulls", () => {
    expect(grossNotional([{ notional: 25000 }, { notional: -15000 }, { notional: null }])).toBe(40000);
  });
});

/* ─── Panel ──────────────────────────────────────────── */

const mockUseQuant = vi.fn();
vi.mock("@/lib/useQuant", () => ({
  useQuant: (strategy: string) => mockUseQuant(strategy),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, isDesktop: true, hasMounted: true }),
}));

import QuantPanel from "@/components/QuantPanel";

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

afterEach(() => {
  cleanup();
  mockUseQuant.mockReset();
});

function buildSeries(n: number): { dates: string[]; equity: number[] } {
  const dates: string[] = [];
  const equity: number[] = [];
  const start = new Date(Date.UTC(2025, 0, 2));
  let level = 100_000;
  let i = 0;
  while (dates.length < n) {
    const day = new Date(start.getTime() + i * 86_400_000);
    i += 1;
    if (day.getUTCDay() === 0 || day.getUTCDay() === 6) continue;
    level *= 1 + (dates.length % 7 === 3 ? -0.012 : 0.004);
    dates.push(day.toISOString().slice(0, 10));
    equity.push(Math.round(level * 100) / 100);
  }
  return { dates, equity };
}

function buildData(overrides: Partial<QuantData> = {}): QuantData {
  const series = buildSeries(300);
  return {
    strategy: "trend_inverse_vol",
    label: "Trend, inverse vol (futures proxy)",
    missing: false,
    run: {
      run_at: "2026-10-30T22:00:00Z",
      since: series.dates[0],
      until: series.dates[series.dates.length - 1],
      n_sessions: series.dates.length,
      n_rebalances: 14,
      cost_bps: 5,
      universe: ["SPY", "QQQ", "IEF", "USO", "GLD", "CORN"],
      metrics: {
        sharpe: 0.93,
        sortino: 1.2,
        calmar: 0.61,
        max_drawdown: -0.097,
        annualized_return: 0.059,
        ann_vol: 0.063,
      },
      final_weights: { SPY: 0.3, QQQ: 0.2, IEF: -0.5 },
      dates: series.dates,
      equity: series.equity,
    },
    targets: {
      as_of: "2026-10-30",
      recorded_at: "2026-10-30T21:25:00Z",
      rebalance_today: true,
      rows: [
        { symbol: "SPY", weight: 0.3, notional: 30000, close: 500 },
        { symbol: "QQQ", weight: 0.2, notional: 20000, close: 480 },
        { symbol: "IEF", weight: -0.5, notional: -50000, close: 95 },
      ],
    },
    orders: {
      as_of: "2026-10-30",
      placed_at: "2026-10-31T13:35:00Z",
      rows: [{ symbol: "SPY", action: "BUY", quantity: 60, limit_price: 501, status: "planned", message: "dry run" }],
    },
    ...overrides,
  };
}

function hookState(partial: Partial<{
  data: QuantData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
}> = {}) {
  return {
    data: null as QuantData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseQuant.mockReturnValue(state);
  return render(<QuantPanel />);
}

describe("QuantPanel — gating and chips", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading quant sleeve")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the settled missing case", () => {
    renderPanel(hookState({ data: QUANT_MISSING("trend_inverse_vol") }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No quant sleeve runs yet")).toBeTruthy();
  });

  it("renders one chip per registered strategy and asks the hook for the default first", () => {
    renderPanel(hookState({ data: buildData() }));
    for (const s of QUANT_STRATEGIES) {
      expect(screen.getByRole("button", { name: s.label })).toBeTruthy();
    }
    expect(mockUseQuant).toHaveBeenCalledWith("trend_inverse_vol");
  });

  it("re-requests the selected strategy when a chip is clicked", () => {
    renderPanel(hookState({ data: buildData() }));
    fireEvent.click(screen.getByRole("button", { name: "Monthly inverse volatility" }));
    expect(mockUseQuant).toHaveBeenLastCalledWith("inverse_vol");
    expect(screen.getByRole("button", { name: "Monthly inverse volatility" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("QuantPanel — strip, chart, tables", () => {
  it("renders the backtest metrics and the as-of session", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("quant-strip-return").textContent).toContain("+5.9%");
    expect(screen.getByTestId("quant-strip-sharpe").textContent).toContain("0.93");
    expect(screen.getByTestId("quant-strip-maxdd").textContent).toContain("-9.7%");
    expect(screen.getByTestId("quant-strip-vol").textContent).toContain("6.3%");
    expect(screen.getByTestId("quant-strip-asof").textContent).toContain("2026-10-30");
    expect(screen.getByTestId("quant-rebalance-flag").textContent).toBe("REBALANCE TODAY");
  });

  it("titles the chart after the selected strategy and draws no NaN paths", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("QUANT SLEEVE EQUITY TREND, INVERSE VOL (FUTURES PROXY)")).toBeTruthy();
    const paths = Array.from(container.querySelectorAll("path"));
    expect(paths.some((p) => (p.getAttribute("d") ?? "").includes("NaN"))).toBe(false);
    expect(screen.getByTestId("quant-brush")).toBeTruthy();
  });

  it("lists targets with weight, notional and the whole-share estimate", () => {
    renderPanel(hookState({ data: buildData() }));
    const ief = screen.getByTestId("quant-target-IEF");
    expect(ief.textContent).toContain("-0.500");
    expect(ief.textContent).toContain("-$50,000");
    expect(ief.textContent).toContain("-526");
    expect(screen.getByTestId("quant-targets-gross").textContent).toContain("$100,000");
  });

  it("renders the execution plan when orders exist and hides it otherwise", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("quant-orders-table").textContent).toContain("PLANNED");
    cleanup();
    renderPanel(hookState({ data: buildData({ orders: null }) }));
    expect(screen.queryByTestId("quant-orders-section")).toBeNull();
  });

  it("mounts the freshness rail on the targets as-of session", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("quant-freshness-rail")).toBeTruthy();
    expect(screen.getByTestId("quant-strip-asof-rail").textContent).toContain("2026-10-30");
  });

  it("falls back to the run's last session when no targets exist", () => {
    renderPanel(hookState({ data: buildData({ targets: null }) }));
    expect(screen.getByTestId("quant-rebalance-flag").textContent).toBe("NO TARGETS YET");
    expect(screen.getByText("No target weights have been written for this strategy yet.")).toBeTruthy();
  });
});
