/**
 * @vitest-environment jsdom
 *
 * MSR regime tab: move surprise ratio for SPX / NDX / RUT.
 *
 * Pure helpers (lib/msr.ts): 3dp ratio formatting, "#n of count" ranks,
 * strict p90/p99 tones, signed move %, per-index chart rows with nulls
 * dropped.
 *
 * MsrPanel: gating (SpectralLoader / SectionEmptyState), index chips, the
 * header strip, the freshness rail, the chart title, the top-10 table and
 * the NaN path guard. Spec: docs/indicators/msr.md.
 */
import React from "react";
import { expectStripFillsGrid } from "./helpers/stripFit";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  MSR_INDEXES,
  buildMsrChartRows,
  formatMovePct,
  formatMsr,
  formatRank,
  msrTone,
  type MsrData,
  type MsrIndexSummary,
  type MsrRow,
} from "@/lib/msr";

/* ─── Pure helpers ───────────────────────────────────── */

const SPX_STATS = {
  count: 4779,
  max: 0.262231,
  max_date: "2026-11-05",
  p50: 0.029424,
  p90: 0.081531,
  p99: 0.149252,
};

describe("MSR_INDEXES", () => {
  it("lists the three benchmarks in display order", () => {
    expect([...MSR_INDEXES]).toEqual(["SPX", "NDX", "RUT"]);
  });
});

describe("formatMsr — three decimals", () => {
  it("formats ratios to 3 dp", () => {
    expect(formatMsr(0.262231)).toBe("0.262");
    expect(formatMsr(0.011162)).toBe("0.011");
    expect(formatMsr(0)).toBe("0.000");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatMsr(null)).toBe("---");
    expect(formatMsr(undefined)).toBe("---");
    expect(formatMsr(Number.NaN)).toBe("---");
    expect(formatMsr(Number.POSITIVE_INFINITY)).toBe("---");
  });
});

describe("formatRank", () => {
  it("renders #rank of count with thousands separators", () => {
    expect(formatRank(1, 4779)).toBe("#1 of 4,779");
    expect(formatRank(3736, 4779)).toBe("#3,736 of 4,779");
    expect(formatRank(835, 1492)).toBe("#835 of 1,492");
  });

  it("returns --- when either side is missing", () => {
    expect(formatRank(null, 4779)).toBe("---");
    expect(formatRank(1, null)).toBe("---");
    expect(formatRank(undefined, undefined)).toBe("---");
  });
});

describe("msrTone — strict p90 / p99 inequalities", () => {
  it("above p99 reads negative (vol badly underpriced the move)", () => {
    expect(msrTone(0.149253, SPX_STATS)).toBe("var(--negative)");
    expect(msrTone(0.262231, SPX_STATS)).toBe("var(--negative)");
  });

  it("exactly p99 is not above it: warning", () => {
    expect(msrTone(0.149252, SPX_STATS)).toBe("var(--warning)");
  });

  it("above p90 reads warning", () => {
    expect(msrTone(0.081532, SPX_STATS)).toBe("var(--warning)");
  });

  it("exactly p90 and below read muted", () => {
    expect(msrTone(0.081531, SPX_STATS)).toBe("var(--text-muted)");
    expect(msrTone(0.011162, SPX_STATS)).toBe("var(--text-muted)");
  });

  it("null value or null stats read muted", () => {
    expect(msrTone(null, SPX_STATS)).toBe("var(--text-muted)");
    expect(msrTone(0.5, null)).toBe("var(--text-muted)");
  });
});

describe("formatMovePct — signed 2 dp", () => {
  it("signs both directions", () => {
    expect(formatMovePct(-4.355658)).toBe("-4.36%");
    expect(formatMovePct(0.314534)).toBe("+0.31%");
    expect(formatMovePct(5.543447)).toBe("+5.54%");
  });

  it("returns --- for missing", () => {
    expect(formatMovePct(null)).toBe("---");
    expect(formatMovePct(undefined)).toBe("---");
    expect(formatMovePct(Number.NaN)).toBe("---");
  });
});

describe("buildMsrChartRows — one index, nulls dropped", () => {
  const series: MsrRow[] = [
    { date: "2007-11-08", spx: 0.002175, ndx: null, rut: null },
    { date: "2009-09-15", spx: 0.01, ndx: 0.02, rut: null },
    { date: "2020-11-30", spx: 0.03, ndx: 0.04, rut: 0.066147 },
  ];

  it("selects the requested index", () => {
    expect(buildMsrChartRows(series, "SPX")).toEqual([
      { date: "2007-11-08", msr: 0.002175 },
      { date: "2009-09-15", msr: 0.01 },
      { date: "2020-11-30", msr: 0.03 },
    ]);
  });

  it("drops rows where that index is null", () => {
    expect(buildMsrChartRows(series, "NDX")).toEqual([
      { date: "2009-09-15", msr: 0.02 },
      { date: "2020-11-30", msr: 0.04 },
    ]);
    expect(buildMsrChartRows(series, "RUT")).toEqual([{ date: "2020-11-30", msr: 0.066147 }]);
  });

  it("returns [] for an empty series", () => {
    expect(buildMsrChartRows([], "SPX")).toEqual([]);
  });
});

/* ─── MsrPanel component ─────────────────────────────── */

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

const mockUseMsr = vi.fn();
vi.mock("@/lib/useMsr", () => ({
  useMsr: (...args: unknown[]) => mockUseMsr(...args),
}));

import MsrPanel from "../components/MsrPanel";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  mockUseMsr.mockReset();
});

function buildSeries(count: number): MsrRow[] {
  return Array.from({ length: count }, (_, i) => {
    const day = new Date(Date.UTC(2026, 0, 5 + i));
    return {
      date: day.toISOString().slice(0, 10),
      spx: 0.01 + (i % 9) * 0.01,
      ndx: i < 10 ? null : 0.02 + (i % 7) * 0.01,
      rut: i % 4 === 0 ? null : 0.015 + (i % 5) * 0.01,
    };
  });
}

function spxSummary(overrides: Partial<MsrIndexSummary> = {}): MsrIndexSummary {
  return {
    iv_symbol: "VIX",
    current: {
      date: "2026-11-06",
      msr: 0.011162,
      move_pct: 0.314534,
      close: 7376.86,
      iv_prior: 28.18,
      rank: 3736,
    },
    week_max: { date: "2026-11-05", msr: 0.262231, move_pct: -4.355658, iv_prior: 16.61, rank: 1 },
    stats: SPX_STATS,
    top: [
      { date: "2026-11-05", msr: 0.262231, move_pct: -4.355658 },
      { date: "2008-09-29", msr: 0.253577, move_pct: -8.809251 },
      { date: "2018-02-05", msr: 0.236737, move_pct: -4.097924 },
    ],
    ...overrides,
  };
}

function ndxSummary(): MsrIndexSummary {
  return {
    iv_symbol: "VXN",
    current: {
      date: "2026-11-06",
      msr: 0.033333,
      move_pct: 1.134,
      close: 25500.12,
      iv_prior: 34.02,
      rank: 410,
    },
    week_max: { date: "2026-11-05", msr: 0.201, move_pct: -5.12, iv_prior: 25.47, rank: 2 },
    stats: { count: 4313, max: 0.244, max_date: "2020-03-16", p50: 0.03, p90: 0.08, p99: 0.15 },
    top: [{ date: "2020-03-16", msr: 0.244, move_pct: -12.32 }],
  };
}

function rutSummary(): MsrIndexSummary {
  return {
    iv_symbol: "RVX",
    current: {
      date: "2026-11-06",
      msr: 0.030719,
      move_pct: 0.887169,
      close: 2754.1054,
      iv_prior: 28.88,
      rank: 835,
    },
    week_max: { date: "2026-11-05", msr: 0.225762, move_pct: -4.447506, iv_prior: 19.7, rank: 3 },
    stats: { count: 1492, max: 0.263688, max_date: "2023-11-14", p50: 0.035047, p90: 0.08421, p99: 0.146056 },
    top: [
      { date: "2023-11-14", msr: 0.263688, move_pct: 5.442515 },
      { date: "2025-04-03", msr: 0.258181, move_pct: -6.591361 },
    ],
  };
}

function buildData(overrides: Partial<MsrData> = {}): MsrData {
  return {
    scan_time: "2026-11-09T03:00:00Z",
    start_date: "2007-11-08",
    source_last_modified: {
      spx: "Sat, 07 Nov 2026 02:11:00 GMT",
      vix: "Sat, 07 Nov 2026 02:21:00 GMT",
      vxn: "Fri, 06 Nov 2026 22:31:00 GMT",
      rvx: "Fri, 06 Nov 2026 22:31:00 GMT",
    },
    close_sources: { SPX: ["cboe"], NDX: ["ib"], RUT: ["ib", "yahoo"] },
    indexes: { SPX: spxSummary(), NDX: ndxSummary(), RUT: rutSummary() },
    series: buildSeries(120),
    ...overrides,
  };
}

function hookState(partial: Partial<{
  data: MsrData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
}> = {}) {
  return {
    data: null as MsrData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseMsr.mockReturnValue(state);
  return render(<MsrPanel />);
}

describe("MsrPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading Cboe MSR series")).toBeTruthy();
  });

  it("shows the SpectralLoader while syncing without data", () => {
    renderPanel(hookState({ syncing: true }));
    expect(screen.getByText("Loading Cboe MSR series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the settled missing case", () => {
    renderPanel(
      hookState({
        data: { missing: true, scan_time: null, start_date: null, indexes: null, series: [] },
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No MSR data yet")).toBeTruthy();
  });
});

describe("MsrPanel — index chips", () => {
  it("renders SPX / NDX / RUT chips with SPX pressed by default", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("msr-index-SPX").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("msr-index-NDX").getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTestId("msr-index-RUT").getAttribute("aria-pressed")).toBe("false");
  });

  it("switching chips moves aria-pressed and the strip to that index", () => {
    renderPanel(hookState({ data: buildData() }));
    fireEvent.click(screen.getByTestId("msr-index-RUT"));
    expect(screen.getByTestId("msr-index-RUT").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("msr-index-SPX").getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTestId("msr-last-value").textContent).toBe("0.031");
    expect(screen.getByText("#835 of 1,492")).toBeTruthy();
    expect(screen.getByText("RVX PRIOR")).toBeTruthy();
  });
});

describe("MsrPanel — header strip", () => {
  it("renders the selected index's last MSR, rank, 5D max and move", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("msr-last-value").textContent).toBe("0.011");
    expect(screen.getByText("#3,736 of 4,779")).toBeTruthy();
    const week = screen.getByTestId("msr-week-max").textContent ?? "";
    expect(week).toContain("0.262");
    expect(week).toContain("#1");
    expect(screen.getByText("+0.31%")).toBeTruthy(); // current move
    expect(screen.getByText("VIX PRIOR")).toBeTruthy();
  });

  it("shows the vix Last-Modified as the source date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("07 Nov 2026")).toBeTruthy();
  });

  it("tones the last value muted inside p90", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("msr-last-value").style.color).toBe("var(--text-muted)");
  });

  it("tones the last value negative above p99", () => {
    const spx = spxSummary({
      current: {
        date: "2026-11-05", msr: 0.262231, move_pct: -4.355658,
        close: 7353.73, iv_prior: 16.61, rank: 1,
      },
    });
    renderPanel(hookState({ data: buildData({ indexes: { ...buildData().indexes!, SPX: spx } }) }));
    expect(screen.getByTestId("msr-last-value").style.color).toBe("var(--negative)");
  });

  it("renders --- for an index whose closes are unavailable", () => {
    const empty: MsrIndexSummary = { iv_symbol: "VXN", current: null, week_max: null, stats: null, top: [] };
    renderPanel(hookState({ data: buildData({ indexes: { ...buildData().indexes!, NDX: empty } }) }));
    fireEvent.click(screen.getByTestId("msr-index-NDX"));
    expect(screen.getByTestId("msr-last-value").textContent).toBe("---");
  });

  it("fills the strip grid with no empty cells", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    expectStripFillsGrid(container);
  });
});

describe("MsrPanel — freshness rail", () => {
  it("shows the held session and counts down to the 03:00 UTC slot", () => {
    // 00:00 UTC: 3h short of radon-msr.timer's 03:00 UTC slot. The neighbour
    // daily timers (02:50, 03:10) would read 2h 50m / 3h 10m, so the
    // constant is pinned, not just "some daily timer".
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-10T00:00:00Z"));
    const data = buildData();
    const spx = spxSummary({ current: { ...data.indexes!.SPX.current!, date: "2026-11-09" } });
    renderPanel(hookState({ data: { ...data, indexes: { ...data.indexes!, SPX: spx } } }));
    act(() => {
      vi.advanceTimersByTime(0);
    });
    const rail = screen.getByTestId("msr-freshness-rail");
    expect(rail).toBeTruthy();
    expect(screen.getByTestId("msr-strip-asof").textContent).toContain("2026-11-09");
    expect(screen.getByTestId("msr-freshness-rail-countdown").textContent).toBe("3h 00m");
    expect(rail.textContent).toContain("Next sample");
  });
});

describe("MsrPanel — chart", () => {
  it("titles the chart after the selected index", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("SPX MOVE SURPRISE RATIO")).toBeTruthy();
    fireEvent.click(screen.getByTestId("msr-index-NDX"));
    expect(screen.getByText("NDX MOVE SURPRISE RATIO")).toBeTruthy();
    expect(screen.queryByText("SPX MOVE SURPRISE RATIO")).toBeNull();
  });

  it("does not emit NaN path coordinates with null rows present", () => {
    renderPanel(hookState({ data: buildData() }));
    for (const index of ["SPX", "NDX", "RUT"]) {
      fireEvent.click(screen.getByTestId(`msr-index-${index}`));
      const paths = document.querySelectorAll("path");
      for (const path of Array.from(paths)) {
        expect(path.getAttribute("d") ?? "").not.toContain("NaN");
      }
    }
  });
});

describe("MsrPanel — top table", () => {
  it("lists the selected index's largest readings", () => {
    renderPanel(hookState({ data: buildData() }));
    const table = screen.getByTestId("msr-top-table");
    const rows = table.querySelectorAll("tbody tr");
    expect(rows.length).toBe(3);
    expect(within(rows[0] as HTMLElement).getByText("2026-11-05")).toBeTruthy();
    expect(within(rows[0] as HTMLElement).getByText("0.262")).toBeTruthy();
    expect(within(rows[0] as HTMLElement).getByText("-4.36%")).toBeTruthy();
    expect(within(table).getByText("2008-09-29")).toBeTruthy();
  });

  it("follows the index chip", () => {
    renderPanel(hookState({ data: buildData() }));
    fireEvent.click(screen.getByTestId("msr-index-RUT"));
    const table = screen.getByTestId("msr-top-table");
    expect(table.querySelectorAll("tbody tr").length).toBe(2);
    expect(within(table).getByText("2023-11-14")).toBeTruthy();
    expect(within(table).queryByText("2008-09-29")).toBeNull();
  });
});
