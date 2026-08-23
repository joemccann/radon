/**
 * @vitest-environment jsdom
 *
 * DIV YIELD — percent of S&P 500 stocks with trailing dividend yield above the
 * 10-Year Treasury yield (regime tab).
 *
 * Pure helpers (lib/divyield.ts): the regime label with its exact half-open
 * boundary pins (spec threshold table) and the frozen missing contract.
 *
 * DivYieldPanel: gating (SpectralLoader / SectionEmptyState), the header
 * strip, the chart title, the range chips (default All on the multi-decade
 * monthly series), the brush minimap, the NaN guard, and copy discipline
 * (no em dashes, no cadence claims).
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  MISSING_DIVYIELD,
  divYieldRegimeLabel,
  type DivYieldData,
  type DivYieldPoint,
} from "@/lib/divyield";

/* ─── Pure helpers ───────────────────────────────────── */

describe("divYieldRegimeLabel — half-open threshold table", () => {
  it("classifies the interior of each band", () => {
    expect(divYieldRegimeLabel(0)).toBe("SCARCE");
    expect(divYieldRegimeLabel(3.78)).toBe("SCARCE");
    expect(divYieldRegimeLabel(10)).toBe("LOW");
    expect(divYieldRegimeLabel(18)).toBe("NEUTRAL");
    expect(divYieldRegimeLabel(40)).toBe("ELEVATED");
    expect(divYieldRegimeLabel(100)).toBe("EXTREME");
  });

  it("boundary values belong to the band above (spec pins)", () => {
    expect(divYieldRegimeLabel(4.99)).toBe("SCARCE");
    expect(divYieldRegimeLabel(5)).toBe("LOW");
    expect(divYieldRegimeLabel(14.99)).toBe("LOW");
    expect(divYieldRegimeLabel(15)).toBe("NEUTRAL");
    expect(divYieldRegimeLabel(34.99)).toBe("NEUTRAL");
    expect(divYieldRegimeLabel(35)).toBe("ELEVATED");
    expect(divYieldRegimeLabel(49.99)).toBe("ELEVATED");
    expect(divYieldRegimeLabel(50)).toBe("EXTREME");
  });
});

describe("missing contract", () => {
  it("freezes the exact HTTP-200 missing shape", () => {
    expect(MISSING_DIVYIELD).toEqual({
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      series: [],
      backfill_cutover: null,
    });
    expect(Object.isFrozen(MISSING_DIVYIELD)).toBe(true);
  });
});

/* ─── Panel ──────────────────────────────────────────── */

// jsdom ships no ResizeObserver; CriHistoryChart wires one up on mount.
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

const mockUseDivYield = vi.fn();
vi.mock("@/lib/useDivYield", () => ({
  useDivYield: (...args: unknown[]) => mockUseDivYield(...args),
}));

import DivYieldPanel from "../components/DivYieldPanel";

afterEach(() => {
  cleanup();
  mockUseDivYield.mockReset();
});

// Window-relative dates: the series always ends "yesterday", never a
// hardcoded date. 440 points keeps the 1Y preset visible and All meaningful.
const DAY_MS = 86_400_000;
const SERIES_LENGTH = 440;
const DATA_DATE = new Date(Date.now() - DAY_MS).toISOString().slice(0, 10);

function seriesDate(index: number): string {
  return new Date(Date.now() - (SERIES_LENGTH - index) * DAY_MS).toISOString().slice(0, 10);
}

function buildSeries(length = SERIES_LENGTH): DivYieldPoint[] {
  return Array.from({ length }, (_, i) => ({
    date: i === length - 1 ? DATA_DATE : seriesDate(i),
    pct_above: 5 + 20 * Math.abs(Math.sin(i / 40)),
    count_above: 25 + (i % 100),
    total: 480 + (i % 24),
    y10: 4 + (i % 10) * 0.1,
    approximate: i < length / 2 ? 1 : 0,
  }));
}

function buildData(overrides: Partial<DivYieldData> = {}): DivYieldData {
  const series = buildSeries();
  return {
    scan_time: new Date().toISOString(),
    source: { constituents: "github-datasets", constituents_count: 503, quote_errors: 0 },
    data_date: DATA_DATE,
    y10_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      pct_above: 3.78,
      count_above: 19,
      total: 503,
      y10: 4.74,
      leaders: [{ ticker: "TDG", yield_pct: 7.5 }],
    },
    series,
    backfill_cutover: series[Math.floor(SERIES_LENGTH / 2)].date,
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: DivYieldData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as DivYieldData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseDivYield.mockReturnValue(state);
  return render(<DivYieldPanel />);
}

describe("DivYieldPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading dividend yield breadth series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the missing contract", () => {
    renderPanel(hookState({ data: { ...MISSING_DIVYIELD } as unknown as DivYieldData }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });
});

describe("DivYieldPanel — header strip", () => {
  it("renders pct above, count, 10Y yield, regime, and source date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("divyield-above").textContent).toBe("3.78%");
    expect(screen.getByTestId("divyield-count").textContent).toBe("19 / 503");
    expect(screen.getByTestId("divyield-y10").textContent).toBe("4.74%");
    expect(screen.getByTestId("divyield-regime").textContent).toBe("SCARCE");
    expect(screen.getByTestId("divyield-updated").textContent).toContain(DATA_DATE);
  });

  it("labels an elevated reading through the shared regime helper", () => {
    const data = buildData();
    data.current = { ...data.current!, pct_above: 35, count_above: 176 };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("divyield-regime").textContent).toBe("ELEVATED");
  });
});

describe("DivYieldPanel — chart + controls", () => {
  it("renders the chart title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("PCT OF SPX STOCKS YIELDING ABOVE 10Y")).toBeTruthy();
  });

  it("defaults the range chips to All on the multi-decade series", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "1Y" })).toBeTruthy();
  });

  it("renders the brush minimap with the divyield testid prefix", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("divyield-brush")).toBeTruthy();
  });

  it("never emits NaN into chart paths", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

describe("DivYieldPanel — copy discipline", () => {
  it("contains no em dashes and no cadence claims in its copy", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const text = container.textContent ?? "";
    expect(text).not.toContain("—");
    expect(text).not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly|every)/i);
  });
});
