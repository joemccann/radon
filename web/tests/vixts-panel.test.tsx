/**
 * @vitest-environment jsdom
 *
 * VIX TS — VIX / VIX3M term-structure ratio (regime tab).
 *
 * Pure helpers (lib/vixts.ts): vixTsRegimeLabel with its strict threshold
 * table (>= belongs to the band above; null/NaN pins an explicit default),
 * formatRatio, formatIndex, and the frozen missing contract.
 *
 * VixTsPanel: gating (SpectralLoader / SectionEmptyState), the five-cell
 * header strip, the chart title, the range chips (default All on the daily
 * series back to 2009), the brush minimap, the NaN guard, and copy
 * discipline (no em dashes, no cadence claims).
 *
 * Spec: docs/indicators/vixts.md.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  MISSING_VIXTS,
  formatIndex,
  formatRatio,
  vixTsRegimeLabel,
  type VixTsData,
  type VixTsPoint,
} from "@/lib/vixts";

/* ─── Pure helpers ───────────────────────────────────── */

describe("vixTsRegimeLabel — strict threshold table", () => {
  it("labels the band interiors", () => {
    expect(vixTsRegimeLabel(1.2739)).toBe("BACKWARDATION");
    expect(vixTsRegimeLabel(0.97)).toBe("FLAT");
    expect(vixTsRegimeLabel(0.8455)).toBe("CONTANGO");
    expect(vixTsRegimeLabel(0.7104)).toBe("STEEP CONTANGO");
  });

  it("assigns each boundary to the band above", () => {
    expect(vixTsRegimeLabel(1.0)).toBe("BACKWARDATION");
    expect(vixTsRegimeLabel(0.9999)).toBe("FLAT");
    expect(vixTsRegimeLabel(0.95)).toBe("FLAT");
    expect(vixTsRegimeLabel(0.9499)).toBe("CONTANGO");
    expect(vixTsRegimeLabel(0.8)).toBe("CONTANGO");
    expect(vixTsRegimeLabel(0.7999)).toBe("STEEP CONTANGO");
  });

  it("pins an explicit default for null/NaN (a chained-if would leak NaN to a real band)", () => {
    expect(vixTsRegimeLabel(null)).toBe("CONTANGO");
    expect(vixTsRegimeLabel(Number.NaN)).toBe("CONTANGO");
  });
});

describe("formatRatio", () => {
  it("renders the ratio to four decimals", () => {
    expect(formatRatio(0.8455)).toBe("0.8455");
    expect(formatRatio(0.848435)).toBe("0.8484");
    expect(formatRatio(1)).toBe("1.0000");
  });

  it("renders '---' for null and NaN", () => {
    expect(formatRatio(null)).toBe("---");
    expect(formatRatio(Number.NaN)).toBe("---");
  });
});

describe("formatIndex", () => {
  it("renders an index level to two decimals", () => {
    expect(formatIndex(15.21)).toBe("15.21");
    expect(formatIndex(17.99)).toBe("17.99");
  });

  it("renders '---' for null and NaN", () => {
    expect(formatIndex(null)).toBe("---");
    expect(formatIndex(Number.NaN)).toBe("---");
  });
});

describe("missing contract", () => {
  it("freezes the exact HTTP-200 missing shape", () => {
    expect(MISSING_VIXTS).toEqual({
      missing: true,
      scan_time: null,
      source_last_modified: null,
      data_date: null,
      current: null,
      stats: null,
      series: [],
    });
    expect(Object.isFrozen(MISSING_VIXTS)).toBe(true);
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

const mockUseVixTs = vi.fn();
vi.mock("@/lib/useVixTs", () => ({
  useVixTs: (...args: unknown[]) => mockUseVixTs(...args),
}));

import VixTsPanel from "../components/VixTsPanel";

afterEach(() => {
  cleanup();
  mockUseVixTs.mockReset();
});

// Window-relative dates: the series always ends on the last completed
// session, never a hardcoded date. 600 points keeps the 1Y preset meaningful.
const SERIES_LENGTH = 600;

function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const DATA_DATE = daysAgo(1);

function buildSeries(length = SERIES_LENGTH): VixTsPoint[] {
  return Array.from({ length }, (_, i) => ({
    date: daysAgo(length - i),
    vix: 15 + 3 * Math.sin(i / 20),
    vix3m: 18 + 2 * Math.sin(i / 30),
    // Smooth, always inside the plausible 0.40..2.50 band; never NaN.
    ratio: 0.88 + 0.12 * Math.sin(i / 25),
    spx: 6800 + 900 * Math.sin(i / 40),
  }));
}

function buildData(overrides: Partial<VixTsData> = {}): VixTsData {
  return {
    scan_time: new Date().toISOString(),
    source_last_modified: {
      vix: "Thu, 27 Aug 2026 01:50:46 GMT",
      vix3m: "Wed, 26 Aug 2026 22:00:57 GMT",
      spx: "Thu, 27 Aug 2026 00:31:07 GMT",
    },
    data_date: DATA_DATE,
    count: SERIES_LENGTH,
    current: {
      date: DATA_DATE,
      vix: 15.21,
      vix3m: 17.99,
      ratio: 0.8455,
      regime: "CONTANGO",
      spx: 7654.32,
    },
    stats: {
      min: 0.7104,
      max: 1.3437,
      mean: 0.894398,
      median: 0.8846,
      days_backwardation: 325,
      pct_backwardation: 7.6435,
      last_backwardation_date: "2026-04-07",
    },
    series: [
      ...buildSeries().slice(0, -1),
      { date: DATA_DATE, vix: 15.21, vix3m: 17.99, ratio: 0.8455, spx: 7654.32 },
    ],
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: VixTsData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as VixTsData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseVixTs.mockReturnValue(state);
  return render(<VixTsPanel />);
}

describe("VixTsPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading VIX term structure series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the missing contract", () => {
    renderPanel(hookState({ data: { ...MISSING_VIXTS } as unknown as VixTsData }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });
});

describe("VixTsPanel — header strip", () => {
  it("renders ratio, regime, both legs, and the source session", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("vixts-ratio").textContent).toBe("0.8455");
    expect(screen.getByTestId("vixts-regime").textContent).toBe("CONTANGO");
    expect(screen.getByTestId("vixts-vix").textContent).toBe("15.21");
    expect(screen.getByTestId("vixts-vix3m").textContent).toBe("17.99");
    expect(screen.getByTestId("vixts-source-updated").textContent).toBe(DATA_DATE);
  });

  it("labels a backwardated curve via the shared helper", () => {
    const data = buildData();
    data.current = { ...data.current!, vix: 24.5, vix3m: 20.0, ratio: 1.225, regime: "BACKWARDATION" };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("vixts-ratio").textContent).toBe("1.2250");
    expect(screen.getByTestId("vixts-regime").textContent).toBe("BACKWARDATION");
  });

  it("renders '---' cells when the current legs are null", () => {
    const data = buildData();
    data.current = {
      ...data.current!,
      vix: null as unknown as number,
      vix3m: null as unknown as number,
    };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("vixts-vix").textContent).toBe("---");
    expect(screen.getByTestId("vixts-vix3m").textContent).toBe("---");
  });
});

describe("VixTsPanel — chart + controls", () => {
  it("renders the chart title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("VIX TERM STRUCTURE - VIX / VIX3M")).toBeTruthy();
  });

  it("defaults the range chips to All on the daily series", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "1Y" })).toBeTruthy();
  });

  it("renders the brush minimap with the vixts testid prefix", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("vixts-brush")).toBeTruthy();
  });

  it("tolerates a null SPX overlay point without emitting NaN", () => {
    const data = buildData();
    data.series = data.series.map((p, i) =>
      i > data.series.length - 9 ? { ...p, spx: null as unknown as number } : p,
    );
    const { container } = renderPanel(hookState({ data }));
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });

  it("never emits NaN into chart paths across the daily domain", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

describe("VixTsPanel — copy discipline", () => {
  it("contains no em dashes and no cadence claims in its copy", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const text = container.textContent ?? "";
    expect(text).not.toContain("—");
    expect(text).not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly|every)/i);
  });
});
