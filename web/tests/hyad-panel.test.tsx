/**
 * @vitest-environment jsdom
 *
 * HY AD — FINRA TRACE high yield bond cumulative advance-decline line
 * (regime tab).
 *
 * Pure helpers (lib/hyad.ts): hyAdRegimeLabel with its strict-inequality
 * boundary pins (any equality or null MA is MIXED) and the frozen missing
 * contract.
 *
 * HyAdPanel: gating (SpectralLoader / SectionEmptyState), the header strip,
 * the chart title, the range chips (default All on the multi-year series),
 * the brush minimap, the NaN guard, and copy discipline (no em dashes, no
 * cadence claims).
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  MISSING_HYAD,
  hyAdRegimeLabel,
  type HyAdData,
  type HyAdPoint,
} from "@/lib/hyad";

/* ─── Pure helpers ───────────────────────────────────── */

describe("hyAdRegimeLabel — strict inequality table", () => {
  it("labels cum above a rising MA stack as ADVANCING", () => {
    expect(hyAdRegimeLabel(100, 50, 10)).toBe("ADVANCING");
    expect(hyAdRegimeLabel(-100, -500, -900)).toBe("ADVANCING");
  });

  it("labels cum below a falling MA stack as DETERIORATING", () => {
    expect(hyAdRegimeLabel(-2535, -1010, 850)).toBe("DETERIORATING");
    expect(hyAdRegimeLabel(10, 50, 100)).toBe("DETERIORATING");
  });

  it("labels any disagreement between the comparisons as MIXED", () => {
    expect(hyAdRegimeLabel(100, 50, 60)).toBe("MIXED");
    expect(hyAdRegimeLabel(10, 50, 40)).toBe("MIXED");
  });

  it("ANY equality is MIXED (ties never advance or deteriorate)", () => {
    expect(hyAdRegimeLabel(50, 50, 10)).toBe("MIXED");
    expect(hyAdRegimeLabel(100, 50, 50)).toBe("MIXED");
    expect(hyAdRegimeLabel(50, 50, 50)).toBe("MIXED");
    expect(hyAdRegimeLabel(10, 10, 50)).toBe("MIXED");
    expect(hyAdRegimeLabel(10, 50, 50)).toBe("MIXED");
  });

  it("a null MA (not enough history) is MIXED", () => {
    expect(hyAdRegimeLabel(100, null, 10)).toBe("MIXED");
    expect(hyAdRegimeLabel(100, 50, null)).toBe("MIXED");
    expect(hyAdRegimeLabel(100, null, null)).toBe("MIXED");
    expect(hyAdRegimeLabel(null, 50, 10)).toBe("MIXED");
  });
});

describe("missing contract", () => {
  it("freezes the exact HTTP-200 missing shape", () => {
    expect(MISSING_HYAD).toEqual({
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      series: [],
    });
    expect(Object.isFrozen(MISSING_HYAD)).toBe(true);
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

const mockUseHyAd = vi.fn();
vi.mock("@/lib/useHyAd", () => ({
  useHyAd: (...args: unknown[]) => mockUseHyAd(...args),
}));

import HyAdPanel from "../components/HyAdPanel";

afterEach(() => {
  cleanup();
  mockUseHyAd.mockReset();
});

// Window-relative dates: the series always ends "yesterday", never a
// hardcoded date. 440 points keeps the 1Y preset visible and All meaningful.
const DAY_MS = 86_400_000;
const SERIES_LENGTH = 440;
const DATA_DATE = new Date(Date.now() - DAY_MS).toISOString().slice(0, 10);

function seriesDate(index: number): string {
  return new Date(Date.now() - (SERIES_LENGTH - index) * DAY_MS).toISOString().slice(0, 10);
}

function buildSeries(length = SERIES_LENGTH): HyAdPoint[] {
  let cum = 0;
  return Array.from({ length }, (_, i) => {
    const net = Math.round(600 * Math.sin(i / 9));
    cum += net;
    return {
      date: i === length - 1 ? DATA_DATE : seriesDate(i),
      net,
      cum,
      ma21: i >= 20 ? cum - 40 : null,
      ma50: i >= 49 ? cum - 90 : null,
      spx_close: i % 11 === 0 ? null : 5200 + 3 * i,
    };
  });
}

function buildData(overrides: Partial<HyAdData> = {}): HyAdData {
  return {
    scan_time: new Date().toISOString(),
    data_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      advances: 1227,
      declines: 1504,
      unchanged: 69,
      total: 3163,
      net: -277,
      cum: -2535,
      ma21: -1010,
      ma50: 850,
    },
    series: buildSeries(),
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: HyAdData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as HyAdData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseHyAd.mockReturnValue(state);
  return render(<HyAdPanel />);
}

describe("HyAdPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading high yield breadth series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the missing contract", () => {
    renderPanel(hookState({ data: { ...MISSING_HYAD } as unknown as HyAdData }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });
});

describe("HyAdPanel — header strip", () => {
  it("renders cum, net, both MAs, regime, and source date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("hyad-cum").textContent).toBe("-2,535");
    expect(screen.getByTestId("hyad-net").textContent).toBe("-277");
    expect(screen.getByTestId("hyad-ma21").textContent).toContain("-1,010");
    expect(screen.getByTestId("hyad-ma50").textContent).toContain("850");
    expect(screen.getByTestId("hyad-regime").textContent).toBe("DETERIORATING");
    expect(screen.getByTestId("hyad-updated").textContent).toContain(DATA_DATE);
  });

  it("signs a positive net and labels an advancing stack via the shared helper", () => {
    const data = buildData();
    data.current = { ...data.current!, net: 682, cum: 900, ma21: 400, ma50: -200 };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("hyad-net").textContent).toBe("+682");
    expect(screen.getByTestId("hyad-cum").textContent).toBe("+900");
    expect(screen.getByTestId("hyad-regime").textContent).toBe("ADVANCING");
  });

  it("renders MIXED with '---' MAs until enough history exists", () => {
    const data = buildData();
    data.current = { ...data.current!, ma21: null, ma50: null };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("hyad-ma21").textContent).toBe("---");
    expect(screen.getByTestId("hyad-ma50").textContent).toBe("---");
    expect(screen.getByTestId("hyad-regime").textContent).toBe("MIXED");
  });
});

describe("HyAdPanel — chart + controls", () => {
  it("renders the chart title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("HIGH YIELD BOND CUMULATIVE A-D LINE")).toBeTruthy();
  });

  it("defaults the range chips to All on the multi-year series", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "1Y" })).toBeTruthy();
  });

  it("renders the brush minimap with the hyad testid prefix", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("hyad-brush")).toBeTruthy();
  });

  it("never emits NaN into chart paths across null MA and null SPX points", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

describe("HyAdPanel — copy discipline", () => {
  it("contains no em dashes and no cadence claims in its copy", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const text = container.textContent ?? "";
    expect(text).not.toContain("—");
    expect(text).not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly|every)/i);
  });
});
