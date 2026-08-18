/**
 * @vitest-environment jsdom
 *
 * CFTC COT cross-asset positioning — the COT regime tab.
 *
 * Pure helpers (components/equibles-cot/cotPositioning.ts): contract-count
 * formatting, percentile / z-score display, contrarian tone mapping,
 * report-date freshness derived from the payload (never a hardcoded cadence),
 * chart rows, and week-based range presets.
 *
 * EquiblesCotPanel: gating (SpectralLoader / SectionEmptyState), the header
 * strip, contract selection, the chart title, and the cross-asset board.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  CONTRARIAN_TONE,
  COT_RANGE_PRESETS,
  buildCotChartRows,
  cotPresetRange,
  crowdingColor,
  formatContracts,
  formatPercentile,
  formatReportDate,
  formatReportFreshness,
  formatShareOfOi,
  formatZScore,
  reportAgeDays,
  selectContract,
  type CotContract,
  type CotPositioningData,
  type CotWeek,
} from "@/components/equibles-cot/cotPositioning";

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatContracts — signed contract counts", () => {
  it("abbreviates thousands with an explicit sign", () => {
    expect(formatContracts(150_000)).toBe("+150.0k");
    expect(formatContracts(-42_300)).toBe("-42.3k");
  });

  it("keeps small counts unabbreviated", () => {
    expect(formatContracts(940)).toBe("+940");
    expect(formatContracts(0)).toBe("0");
  });

  it("returns --- for null and non-finite values", () => {
    expect(formatContracts(null)).toBe("---");
    expect(formatContracts(undefined)).toBe("---");
    expect(formatContracts(Number.NaN)).toBe("---");
  });
});

describe("formatPercentile / formatZScore / formatShareOfOi", () => {
  it("renders a percentile to one decimal", () => {
    expect(formatPercentile(88.24)).toBe("88.2");
    expect(formatPercentile(null)).toBe("---");
  });

  it("signs the z-score", () => {
    expect(formatZScore(1.84)).toBe("+1.84");
    expect(formatZScore(-0.5)).toBe("-0.50");
    expect(formatZScore(null)).toBe("---");
  });

  it("renders the share of open interest as a percent", () => {
    expect(formatShareOfOi(15)).toBe("+15.0%");
    expect(formatShareOfOi(null)).toBe("---");
  });
});

describe("crowdingColor / CONTRARIAN_TONE — extremes read as warnings", () => {
  it("both crowded extremes are warnings, not health", () => {
    expect(crowdingColor("CROWDED LONG")).toBe("var(--warning)");
    expect(crowdingColor("CROWDED SHORT")).toBe("var(--warning)");
  });

  it("neutral and unknown stay calm", () => {
    expect(crowdingColor("NEUTRAL")).toBe("var(--text-secondary)");
    expect(crowdingColor("UNKNOWN")).toBe("var(--text-muted)");
  });

  it("maps the contrarian bias to brand tones", () => {
    expect(CONTRARIAN_TONE.BEARISH).toBe("var(--negative)");
    expect(CONTRARIAN_TONE.BULLISH).toBe("var(--positive)");
    expect(CONTRARIAN_TONE.NEUTRAL).toBe("var(--text-muted)");
  });
});

describe("report freshness — derived from the payload, never hardcoded", () => {
  const now = new Date("2026-08-11T12:00:00Z");

  it("formats the measured Tuesday", () => {
    expect(formatReportDate("2026-08-04")).toBe("04 Aug 2026");
    expect(formatReportDate(null)).toBe("---");
  });

  it("counts whole days between the report date and now", () => {
    expect(reportAgeDays("2026-08-04", now)).toBe(7);
    expect(reportAgeDays("2026-08-11", now)).toBe(0);
    expect(reportAgeDays(null, now)).toBe(null);
  });

  it("states the age in the label rather than a cadence string", () => {
    expect(formatReportFreshness("2026-08-04", now)).toBe("04 Aug 2026 · 7d old");
    expect(formatReportFreshness("2026-08-11", now)).toBe("11 Aug 2026 · today");
    expect(formatReportFreshness(null, now)).toBe("---");
  });
});

describe("buildCotChartRows — speculators against hedgers", () => {
  const series: CotWeek[] = [
    { report_date: "2026-07-28", net_noncommercial: 100, net_commercial: -100, open_interest: 1000, net_noncommercial_pct_oi: 10 },
    { report_date: "2026-08-04", net_noncommercial: 150, net_commercial: -150, open_interest: 1000, net_noncommercial_pct_oi: 15 },
  ];

  it("maps both nets onto one dated row", () => {
    expect(buildCotChartRows(series)).toEqual([
      { date: "2026-07-28", spec: 100, comm: -100 },
      { date: "2026-08-04", spec: 150, comm: -150 },
    ]);
  });

  it("passes missing legs through as null instead of zero", () => {
    const rows = buildCotChartRows([
      { report_date: "2026-08-04", net_noncommercial: null, net_commercial: null, open_interest: null, net_noncommercial_pct_oi: null },
    ]);
    expect(rows[0].spec).toBe(null);
    expect(rows[0].comm).toBe(null);
  });
});

describe("cotPresetRange — week-based zoom over the weekly series", () => {
  it("6M covers the trailing 26 weeks", () => {
    expect(cotPresetRange("6m", 156)).toEqual([130, 155]);
  });

  it("1Y covers the trailing 52 weeks", () => {
    expect(cotPresetRange("1y", 156)).toEqual([104, 155]);
  });

  it("All covers everything", () => {
    expect(cotPresetRange("all", 156)).toEqual([0, 155]);
  });

  it("clamps presets deeper than the series", () => {
    expect(cotPresetRange("3y", 20)).toEqual([0, 19]);
  });

  it("exposes presets in ascending window order", () => {
    expect(COT_RANGE_PRESETS.map((p) => p.slug)).toEqual(["6m", "1y", "3y", "all"]);
  });
});

describe("selectContract — never strands the panel on a missing alias", () => {
  const contracts = [contract("ES"), contract("GC")];

  it("returns the requested contract", () => {
    expect(selectContract(contracts, "GC")?.alias).toBe("GC");
  });

  it("falls back to the first contract when the alias is gone", () => {
    expect(selectContract(contracts, "ZZZ")?.alias).toBe("ES");
  });

  it("returns null for an empty basket", () => {
    expect(selectContract([], "ES")).toBe(null);
  });
});

/* ─── EquiblesCotPanel ───────────────────────────────── */

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

const mockUseEquiblesCot = vi.fn();
vi.mock("@/components/equibles-cot/useEquiblesCot", () => ({
  useEquiblesCot: (...args: unknown[]) => mockUseEquiblesCot(...args),
}));

import EquiblesCotPanel from "../components/equibles-cot/EquiblesCotPanel";

afterEach(() => {
  cleanup();
  mockUseEquiblesCot.mockReset();
});

const WEEKS_IN_FIXTURE = 60;

/** Most recent COT Tuesday — window-relative so the fixture never rots. */
function latestReportTuesday(): Date {
  const now = new Date();
  const utcDay = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const weekday = new Date(utcDay).getUTCDay(); // Sun=0, Tue=2
  return new Date(utcDay - ((weekday - 2 + 7) % 7) * 86_400_000);
}

function week(index: number): CotWeek {
  const day = new Date(
    latestReportTuesday().getTime() - (WEEKS_IN_FIXTURE - 1 - index) * 7 * 86_400_000,
  );
  return {
    report_date: day.toISOString().slice(0, 10),
    net_noncommercial: 100_000 + index * 1_000,
    net_commercial: -(100_000 + index * 1_000),
    open_interest: 1_000_000,
    net_noncommercial_pct_oi: 10 + index * 0.1,
  };
}

function contract(alias: string, overrides: Partial<CotContract> = {}): CotContract {
  const series = Array.from({ length: WEEKS_IN_FIXTURE }, (_, i) => week(i));
  return {
    alias,
    market_code: `C${alias}`,
    name: `${alias} FUTURES`,
    category: "EquityIndices",
    report_date: series[series.length - 1].report_date,
    weeks: series.length,
    open_interest: 1_000_000,
    net_noncommercial: 159_000,
    net_commercial: -159_000,
    net_noncommercial_pct_oi: 15.9,
    net_noncommercial_change: 1_000,
    percentile: 98.3,
    z_score: 1.84,
    crowding: "CROWDED LONG",
    contrarian_bias: "BEARISH",
    series,
    ...overrides,
  };
}

function buildData(overrides: Partial<CotPositioningData> = {}): CotPositioningData {
  const contracts = [contract("ES"), contract("GC", { crowding: "NEUTRAL", contrarian_bias: "NEUTRAL", percentile: 44.1 })];
  return {
    scan_time: "2026-08-11T22:00:00Z",
    report_date: contracts[0].report_date,
    count: contracts.length,
    lookback_days: 1095,
    min_stats_weeks: 52,
    extremes: { high: 90, low: 10 },
    contracts,
    market: [
      {
        market_code: "CES",
        name: "ES FUTURES",
        category: "EquityIndices",
        report_date: contracts[0].report_date,
        open_interest: 1_000_000,
        net_commercial: -159_000,
        net_noncommercial: 159_000,
        net_noncommercial_pct_oi: 15.9,
      },
    ],
    ...overrides,
  };
}

function hookState(partial: Record<string, unknown> = {}) {
  return {
    data: null as CotPositioningData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseEquiblesCot.mockReturnValue(state);
  return render(<EquiblesCotPanel />);
}

describe("EquiblesCotPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Sampling CFTC speculator positioning")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the settled missing case", () => {
    renderPanel(
      hookState({
        data: {
          missing: true,
          scan_time: null,
          report_date: null,
          count: 0,
          contracts: [],
          market: [],
        } as unknown as CotPositioningData,
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No COT positioning yet")).toBeTruthy();
  });
});

describe("EquiblesCotPanel — header strip", () => {
  it("renders the speculative net, percentile, crowding, and report date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("cot-strip-net").textContent).toContain("+159.0k");
    expect(screen.getByTestId("cot-strip-percentile").textContent).toContain("98.3");
    expect(screen.getByTestId("cot-strip-crowding").textContent).toContain("CROWDED LONG");
    expect(screen.getByTestId("cot-strip-reported").textContent).toContain(
      formatReportDate(latestReportTuesday().toISOString().slice(0, 10)),
    );
  });

  it("tones a crowded extreme as a warning and states the contrarian read", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("cot-crowding-value").style.color).toBe("var(--warning)");
    expect(screen.getByTestId("cot-contrarian-bias").textContent).toContain("BEARISH");
  });
});

describe("EquiblesCotPanel — contract selection", () => {
  it("renders one chip per ingested contract and starts on the first", () => {
    renderPanel(hookState({ data: buildData() }));
    const chips = within(screen.getByTestId("cot-contract-chips")).getAllByRole("button");
    expect(chips.map((c) => c.textContent)).toEqual(["ES", "GC"]);
    expect(chips[0].getAttribute("aria-pressed")).toBe("true");
  });

  it("switches the chart and strip to the selected contract", () => {
    renderPanel(hookState({ data: buildData() }));
    fireEvent.click(screen.getByRole("button", { name: "GC" }));
    expect(screen.getByTestId("cot-strip-percentile").textContent).toContain("44.1");
    expect(screen.getByTestId("cot-crowding-value").style.color).toBe("var(--text-secondary)");
  });
});

describe("EquiblesCotPanel — chart and board", () => {
  it("titles the chart with the selected contract", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText(/ES FUTURES · SPECULATORS VS HEDGERS/i)).toBeTruthy();
  });

  it("renders the range chips and the brush minimap", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("cot-range-chips")).toBeTruthy();
    expect(screen.getByTestId("cot-brush-window")).toBeTruthy();
  });

  it("lists the cross-asset board rows", () => {
    renderPanel(hookState({ data: buildData() }));
    const board = screen.getByTestId("cot-board");
    expect(within(board).getByText("ES FUTURES")).toBeTruthy();
  });

  it("reorders the board when CONTRACT is clicked", () => {
    const report = latestReportTuesday().toISOString().slice(0, 10);
    renderPanel(hookState({
      data: buildData({
        market: [
          { market_code: "CZC", name: "ZC FUTURES", category: "Ag", report_date: report, open_interest: 1, net_commercial: 0, net_noncommercial: 1, net_noncommercial_pct_oi: 1 },
          { market_code: "CES", name: "ES FUTURES", category: "EquityIndices", report_date: report, open_interest: 1, net_commercial: 0, net_noncommercial: 2, net_noncommercial_pct_oi: 2 },
          { market_code: "CGC", name: "GC FUTURES", category: "Metals", report_date: report, open_interest: 1, net_commercial: 0, net_noncommercial: 3, net_noncommercial_pct_oi: 3 },
        ],
      }),
    }));
    const board = screen.getByTestId("cot-board");
    const first = () => within(board).getAllByRole("row")[1].textContent ?? "";
    expect(first()).toContain("ZC FUTURES");
    fireEvent.click(within(board).getByRole("columnheader", { name: /contract/i }));
    expect(first()).toContain("ES FUTURES");
    expect(within(board).getByRole("columnheader", { name: /contract/i }).getAttribute("aria-sort")).toBe("ascending");
  });

  it("hides the board when the latest-week request came back empty", () => {
    renderPanel(hookState({ data: buildData({ market: [] }) }));
    expect(screen.queryByTestId("cot-board")).toBe(null);
  });
});
