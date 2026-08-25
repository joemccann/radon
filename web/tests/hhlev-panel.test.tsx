/**
 * @vitest-environment jsdom
 *
 * HH LEV — US household leverage, percent of net worth (Z.1 B.101 family,
 * regime tab).
 *
 * Pure helpers (lib/hhlev.ts): hhLevRegimeLabel with its half-open threshold
 * table (boundaries belong to the band above; null/NaN pins the explicit
 * DELEVERAGED default), formatQuarter, formatTrillions, and the frozen
 * missing contract.
 *
 * HhLevPanel: gating (SpectralLoader / SectionEmptyState), the header strip,
 * the chart title, the range chips (default All on the 80-year quarterly
 * series), the brush minimap, the NaN guard, and copy discipline (no em
 * dashes, no cadence claims).
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  MISSING_HHLEV,
  formatQuarter,
  formatTrillions,
  hhLevRegimeLabel,
  type HhLevData,
  type HhLevPoint,
} from "@/lib/hhlev";

/* ─── Pure helpers ───────────────────────────────────── */

describe("hhLevRegimeLabel — half-open threshold table", () => {
  it("labels the band interiors", () => {
    expect(hhLevRegimeLabel(3.79)).toBe("DELEVERAGED");
    expect(hhLevRegimeLabel(10.26)).toBe("DELEVERAGED");
    expect(hhLevRegimeLabel(11.78)).toBe("DELEVERAGED");
    expect(hhLevRegimeLabel(13.5)).toBe("MODERATE");
    expect(hhLevRegimeLabel(17.5)).toBe("ELEVATED");
    expect(hhLevRegimeLabel(24.26)).toBe("STRETCHED");
    expect(hhLevRegimeLabel(39)).toBe("STRETCHED");
  });

  it("assigns each boundary to the band above", () => {
    expect(hhLevRegimeLabel(11.99)).toBe("DELEVERAGED");
    expect(hhLevRegimeLabel(12)).toBe("MODERATE");
    expect(hhLevRegimeLabel(15.99)).toBe("MODERATE");
    expect(hhLevRegimeLabel(16)).toBe("ELEVATED");
    expect(hhLevRegimeLabel(19.99)).toBe("ELEVATED");
    expect(hhLevRegimeLabel(20)).toBe("STRETCHED");
  });

  it("pins the explicit DELEVERAGED default for null/NaN (a chained-if would leak NaN to STRETCHED)", () => {
    expect(hhLevRegimeLabel(null)).toBe("DELEVERAGED");
    expect(hhLevRegimeLabel(Number.NaN)).toBe("DELEVERAGED");
  });
});

describe("formatQuarter", () => {
  it("renders a quarter-start date as 'YYYY Qn'", () => {
    expect(formatQuarter("2026-01-01")).toBe("2026 Q1");
    expect(formatQuarter("2009-04-01")).toBe("2009 Q2");
    expect(formatQuarter("1985-07-01")).toBe("1985 Q3");
    expect(formatQuarter("1945-10-01")).toBe("1945 Q4");
  });
});

describe("formatTrillions", () => {
  it("renders $-millions levels as one-decimal trillions", () => {
    // The spec's own current row: 21560050 $M and 182979889 $M.
    expect(formatTrillions(21560050)).toBe("$21.6T");
    expect(formatTrillions(182979889)).toBe("$183.0T");
  });

  it("renders '---' for null and NaN", () => {
    expect(formatTrillions(null)).toBe("---");
    expect(formatTrillions(Number.NaN)).toBe("---");
  });
});

describe("missing contract", () => {
  it("freezes the exact HTTP-200 missing shape", () => {
    expect(MISSING_HHLEV).toEqual({
      missing: true,
      scan_time: null,
      source_last_modified: null,
      data_date: null,
      current: null,
      series: [],
    });
    expect(Object.isFrozen(MISSING_HHLEV)).toBe(true);
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

const mockUseHhLev = vi.fn();
vi.mock("@/lib/useHhLev", () => ({
  useHhLev: (...args: unknown[]) => mockUseHhLev(...args),
}));

import HhLevPanel from "../components/HhLevPanel";

afterEach(() => {
  cleanup();
  mockUseHhLev.mockReset();
});

// Window-relative dates: the series always ends on the last completed
// quarter, never a hardcoded date. 320 quarters spans the 80-year domain
// while keeping the 1Y preset meaningful (4 quarterly points inside it).
const SERIES_LENGTH = 320;

function quarterStart(quartersBack: number): string {
  const now = new Date();
  const total = now.getUTCFullYear() * 4 + Math.floor(now.getUTCMonth() / 3) - quartersBack;
  const year = Math.floor(total / 4);
  const month = (total % 4) * 3 + 1;
  return `${year}-${String(month).padStart(2, "0")}-01`;
}

const DATA_DATE = quarterStart(1);
const DATA_QUARTER_LABEL = (() => {
  const [year, month] = DATA_DATE.split("-").map(Number);
  return `${year} Q${Math.floor((month - 1) / 3) + 1}`;
})();

function buildSeries(length = SERIES_LENGTH): HhLevPoint[] {
  return Array.from({ length }, (_, i) => ({
    date: quarterStart(length - i),
    // Smooth, always inside the plausible 2..40 band; never NaN.
    leverage_pct: 12 + 8 * Math.sin(i / 16),
  }));
}

function buildData(overrides: Partial<HhLevData> = {}): HhLevData {
  return {
    scan_time: new Date().toISOString(),
    source_last_modified: quarterStart(1),
    data_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      leverage_pct: 11.78,
      liabilities_musd: 21560050,
      net_worth_musd: 182979889,
    },
    series: [...buildSeries().slice(0, -1), { date: DATA_DATE, leverage_pct: 11.78 }],
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: HhLevData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as HhLevData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseHhLev.mockReturnValue(state);
  return render(<HhLevPanel />);
}

describe("HhLevPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading household leverage series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the missing contract", () => {
    renderPanel(hookState({ data: { ...MISSING_HHLEV } as unknown as HhLevData }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });
});

describe("HhLevPanel — header strip", () => {
  it("renders leverage, components, regime, and the source quarter", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("hhlev-leverage").textContent).toBe("11.78%");
    expect(screen.getByTestId("hhlev-liab").textContent).toBe("$21.6T");
    expect(screen.getByTestId("hhlev-networth").textContent).toBe("$183.0T");
    expect(screen.getByTestId("hhlev-regime").textContent).toBe("DELEVERAGED");
    expect(screen.getByTestId("hhlev-updated").textContent).toBe(DATA_QUARTER_LABEL);
  });

  it("labels a stretched household balance sheet via the shared helper", () => {
    const data = buildData();
    data.current = {
      ...data.current!,
      leverage_pct: 24.26,
      liabilities_musd: 14363641,
      net_worth_musd: 59208410,
    };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("hhlev-leverage").textContent).toBe("24.26%");
    expect(screen.getByTestId("hhlev-regime").textContent).toBe("STRETCHED");
  });

  it("renders '---' cells when the current components are null", () => {
    const data = buildData();
    data.current = {
      ...data.current!,
      leverage_pct: 11.78,
      liabilities_musd: null as unknown as number,
      net_worth_musd: null as unknown as number,
    };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("hhlev-liab").textContent).toBe("---");
    expect(screen.getByTestId("hhlev-networth").textContent).toBe("---");
  });
});

describe("HhLevPanel — chart + controls", () => {
  it("renders the chart title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("US HOUSEHOLD LEVERAGE PCT OF NET WORTH")).toBeTruthy();
  });

  it("defaults the range chips to All on the 80-year quarterly series", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "1Y" })).toBeTruthy();
  });

  it("renders the brush minimap with the hhlev testid prefix", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("hhlev-brush")).toBeTruthy();
  });

  it("never emits NaN into chart paths across the quarterly domain", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

describe("HhLevPanel — copy discipline", () => {
  it("contains no em dashes and no cadence claims in its copy", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const text = container.textContent ?? "";
    expect(text).not.toContain("—");
    expect(text).not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly|every)/i);
  });
});
