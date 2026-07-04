/**
 * @vitest-environment jsdom
 *
 * Margin Debt Acceleration Indicator — MARGIN regime tab.
 *
 * Pure helpers (lib/marginDebt.ts): $bn formatting, YoY tone thresholds,
 * trailing 3-month MA null semantics, right-series view selection +
 * normalization gating, month-preset ranges.
 *
 * MarginDebtPanel: gating (SpectralLoader / SectionEmptyState), the header
 * stat strip, the chart title, the toggle chips, and the splice footnote.
 */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  buildMarginChartRows,
  formatLevelBn,
  formatMonthTick,
  formatSourceDate,
  formatYoyPct,
  marginPresetRange,
  marginRightViews,
  marginViewSpec,
  movingAverage3,
  yoyColor,
  type MarginDebtData,
  type MarginDebtEntry,
} from "@/lib/marginDebt";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatLevelBn — $mm to $bn display", () => {
  it("formats the current level with a thousands separator and one decimal", () => {
    expect(formatLevelBn(1415557)).toBe("$1,415.6bn");
  });

  it("formats small legacy levels", () => {
    expect(formatLevelBn(3452)).toBe("$3.5bn");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatLevelBn(null)).toBe("---");
    expect(formatLevelBn(undefined)).toBe("---");
    expect(formatLevelBn(Number.NaN)).toBe("---");
  });
});

describe("formatYoyPct / yoyColor — signed display + froth tones", () => {
  it("signs positive and negative values", () => {
    expect(formatYoyPct(53.70450399583044)).toBe("+53.7%");
    expect(formatYoyPct(-12.34)).toBe("-12.3%");
    expect(formatYoyPct(null)).toBe("---");
  });

  it("moderate growth reads positive, extreme growth reads as a warning", () => {
    expect(yoyColor(12)).toBe("var(--positive)");
    expect(yoyColor(49.9)).toBe("var(--positive)");
    expect(yoyColor(50)).toBe("var(--warning)");
    expect(yoyColor(53.7)).toBe("var(--warning)");
  });

  it("contraction reads negative and missing reads muted", () => {
    expect(yoyColor(-8)).toBe("var(--negative)");
    expect(yoyColor(null)).toBe("var(--text-muted)");
  });
});

describe("movingAverage3 — trailing 3-month MA", () => {
  it("averages the trailing window of up to three months", () => {
    expect(movingAverage3([3, 6, 9, 12])).toEqual([3, 4.5, 6, 9]);
  });

  it("keeps null months null (seam gap must not invent data)", () => {
    expect(movingAverage3([10, null, 30])).toEqual([10, null, 20]);
  });

  it("shrinks the window past null neighbours instead of poisoning it", () => {
    expect(movingAverage3([null, null, 30, 60])).toEqual([null, null, 30, 45]);
  });

  it("returns an empty array unchanged", () => {
    expect(movingAverage3([])).toEqual([]);
  });
});

describe("marginRightViews — normalization gating", () => {
  it("hides CPI/GDP-derived views when normalization is unavailable", () => {
    const slugs = marginRightViews(false).map((v) => v.slug);
    expect(slugs).toEqual(["yoy", "level", "net"]);
  });

  it("shows all five views when normalization is available", () => {
    const slugs = marginRightViews(true).map((v) => v.slug);
    expect(slugs).toEqual(["yoy", "level", "net", "gdp", "real"]);
  });

  it("only the splice-adjusted level view is log-scaled", () => {
    for (const view of marginRightViews(true)) {
      expect(view.scaleType).toBe(view.slug === "level" ? "log" : "linear");
    }
  });
});

function entry(overrides: Partial<MarginDebtEntry> = {}): MarginDebtEntry {
  return {
    date: "2026-05",
    level: 1415557,
    free_credit_cash: 206600,
    free_credit_margin: 217256,
    source: "finra",
    level_yoy_pct: 53.7,
    level_display: 1415557,
    net_level: 991701,
    level_real: 1415557,
    level_pct_gdp: null,
    spx_close: 7580.06,
    ...overrides,
  };
}

describe("buildMarginChartRows — right-series selection", () => {
  const series = [
    entry({ date: "2026-03", level_yoy_pct: 30, net_level: 900000, spx_close: 7000 }),
    entry({ date: "2026-04", level_yoy_pct: null, net_level: 950000, spx_close: 7300 }),
    entry({ date: "2026-05", level_yoy_pct: 53.7, net_level: 991701, spx_close: 7580.06 }),
  ];

  it("extracts YoY with nulls preserved and SPX on every row", () => {
    const rows = buildMarginChartRows(series, marginViewSpec("yoy"), false);
    expect(rows.map((r) => r.right)).toEqual([30, null, 53.7]);
    expect(rows.map((r) => r.spx)).toEqual([7000, 7300, 7580.06]);
    expect(rows.map((r) => r.date)).toEqual(["2026-03", "2026-04", "2026-05"]);
  });

  it("converts $mm views to $bn", () => {
    const rows = buildMarginChartRows(series, marginViewSpec("net"), false);
    expect(rows.map((r) => r.right)).toEqual([900, 950, 991.701]);
  });

  it("applies the trailing 3-month MA when smoothing is on", () => {
    const rows = buildMarginChartRows(series, marginViewSpec("yoy"), true);
    expect(rows.map((r) => r.right)).toEqual([30, null, (30 + 53.7) / 2]);
  });
});

describe("marginPresetRange — month-based presets over the full series", () => {
  it("1Y covers the trailing 12 months", () => {
    expect(marginPresetRange("1y", 809)).toEqual([797, 808]);
  });

  it("10Y covers the trailing 120 months", () => {
    expect(marginPresetRange("10y", 809)).toEqual([689, 808]);
  });

  it("All covers everything", () => {
    expect(marginPresetRange("all", 809)).toEqual([0, 808]);
  });

  it("clamps presets deeper than the series", () => {
    expect(marginPresetRange("10y", 24)).toEqual([0, 23]);
  });
});

describe("date formatting", () => {
  it("formats the source Last-Modified header as a compact date", () => {
    expect(formatSourceDate("Tue, 16 Jun 2026 14:52:10 GMT")).toBe("16 Jun 2026");
    expect(formatSourceDate(null)).toBe("---");
    expect(formatSourceDate("not-a-date")).toBe("not-a-date");
  });

  it("formats monthly x-axis ticks as month + year in UTC", () => {
    expect(formatMonthTick(new Date("1997-01"))).toBe("Jan 1997");
    expect(formatMonthTick(new Date("2026-05"))).toBe("May 2026");
  });
});

/* ─── MarginDebtPanel component ──────────────────────── */

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

const mockUseMarginDebt = vi.fn();
vi.mock("@/lib/useMarginDebt", () => ({
  useMarginDebt: (...args: unknown[]) => mockUseMarginDebt(...args),
}));

import MarginDebtPanel from "../components/MarginDebtPanel";

afterEach(() => {
  cleanup();
  mockUseMarginDebt.mockReset();
});

function buildSeries(count: number): MarginDebtEntry[] {
  return Array.from({ length: count }, (_, i) => {
    const year = 2020 + Math.floor(i / 12);
    const month = (i % 12) + 1;
    return entry({
      date: `${year}-${String(month).padStart(2, "0")}`,
      level: 800000 + i * 10000,
      level_display: 800000 + i * 10000,
      net_level: 600000 + i * 8000,
      level_real: 780000 + i * 9000,
      level_pct_gdp: 3 + i * 0.01,
      level_yoy_pct: i < 12 ? null : 10 + i,
      spx_close: 4000 + i * 50,
    });
  });
}

function buildData(overrides: Partial<MarginDebtData> = {}): MarginDebtData {
  const series = buildSeries(30);
  return {
    scan_time: "2026-06-17T09:00:00Z",
    source_last_modified: "Tue, 16 Jun 2026 14:52:10 GMT",
    count: series.length,
    splice: { legacy_source: "nyse_legacy", ratio: 1.039, first_finra_month: "1997-01" },
    normalization: { available: true },
    current: { date: "2026-05", level: 1415557, level_yoy_pct: 53.7 },
    series,
    ...overrides,
  };
}

function hookState(partial: Partial<{
  data: MarginDebtData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
}> = {}) {
  return {
    data: null as MarginDebtData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseMarginDebt.mockReturnValue(state);
  return render(<MarginDebtPanel />);
}

describe("MarginDebtPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading FINRA margin debt series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the settled missing case", () => {
    renderPanel(
      hookState({
        data: {
          missing: true,
          scan_time: null,
          count: 0,
          series: [],
          current: null,
          splice: null,
          normalization: null,
        },
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No margin debt data yet")).toBeTruthy();
  });
});

describe("MarginDebtPanel — header strip", () => {
  it("renders level, YoY, latest month, and source date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("$1,415.6bn")).toBeTruthy();
    expect(screen.getByText("+53.7%")).toBeTruthy();
    expect(screen.getByText("2026-05")).toBeTruthy();
    expect(screen.getByText("16 Jun 2026")).toBeTruthy();
  });

  it("tones extreme YoY growth as froth, not health", () => {
    renderPanel(hookState({ data: buildData() }));
    const yoy = screen.getByTestId("margin-debt-yoy-value");
    expect(yoy.style.color).toBe("var(--warning)");
  });
});

describe("MarginDebtPanel — chart + controls", () => {
  it("renders the chart with the default YoY right series", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("S&P 500 VS MARGIN DEBT YOY %")).toBeTruthy();
  });

  it("renders all five view chips when normalization is available", () => {
    renderPanel(hookState({ data: buildData() }));
    for (const label of ["YOY %", "LEVEL $BN", "NET $BN", "% OF GDP", "REAL $BN"]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
  });

  it("hides the CPI/GDP chips when normalization is unavailable", () => {
    renderPanel(hookState({ data: buildData({ normalization: { available: false } }) }));
    expect(screen.queryByRole("button", { name: "% OF GDP" })).toBeNull();
    expect(screen.queryByRole("button", { name: "REAL $BN" })).toBeNull();
    expect(screen.getByRole("button", { name: "NET $BN" })).toBeTruthy();
  });

  it("switches the right series when a view chip is clicked", () => {
    renderPanel(hookState({ data: buildData() }));
    fireEvent.click(screen.getByRole("button", { name: "NET $BN" }));
    expect(screen.getByText("S&P 500 VS MARGIN DEBT NET $BN")).toBeTruthy();
  });

  it("offers the 3mo MA smoothing toggle", () => {
    renderPanel(hookState({ data: buildData() }));
    const chip = screen.getByRole("button", { name: "3MO MA" });
    expect(chip.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-pressed")).toBe("true");
  });

  it("defaults the range to All on the 65-year series", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
  });

  it("renders the splice footnote", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(
      screen.getByText(
        "FINRA debit balances Jan 1997+; NYSE member-firm series 1959-1996 ratio-adjusted ×1.039 at the splice. YoY is null across the 1997 seam.",
      ),
    ).toBeTruthy();
  });

  it("does not emit NaN path coordinates with the log-scaled level view", () => {
    const data = buildData();
    data.series[0] = entry({ ...data.series[0], level_display: 0 });
    renderPanel(hookState({ data }));
    fireEvent.click(screen.getByRole("button", { name: "LEVEL $BN" }));
    const paths = document.querySelectorAll("path");
    for (const path of Array.from(paths)) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});
