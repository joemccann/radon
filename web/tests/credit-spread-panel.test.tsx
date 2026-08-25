/**
 * @vitest-environment jsdom
 *
 * Credit-equity divergence — CREDIT regime tab.
 *
 * Pure helpers (lib/creditSpread.ts): pct formatting, regime tones,
 * 168-session near-high boundary.
 *
 * CreditSpreadPanel: gating (SpectralLoader / SectionEmptyState), the header
 * stat strip, the chart title, chips, and a NaN guard on the log SPX axis.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  LOOKBACK_SESSIONS,
  NEAR_HIGH_RATIO,
  classifyRegime,
  formatDateTick,
  formatPct,
  formatSessionDate,
  isNearHigh,
  regimeColor,
  type CreditSpreadData,
  type CreditSpreadPoint,
} from "@/lib/creditSpread";

describe("formatPct / formatSessionDate / formatDateTick", () => {
  it("signs the fixture 168-session returns at two decimals", () => {
    expect(formatPct(0.12097839201868865)).toBe("+12.10%");
    expect(formatPct(-0.013025716955806343)).toBe("-1.30%");
    expect(formatPct(null)).toBe("---");
    expect(formatPct(Number.NaN)).toBe("---");
  });

  it("formats the latest session date compactly in UTC", () => {
    expect(formatSessionDate("2026-08-20")).toBe("20 Aug 2026");
    expect(formatSessionDate(null)).toBe("---");
  });

  it("formats daily x-axis ticks as month + year in UTC", () => {
    expect(formatDateTick(new Date("2026-08-20"))).toBe("Aug 2026");
    expect(formatDateTick(new Date("2007-04-11"))).toBe("Apr 2007");
  });
});

describe("classifyRegime / regimeColor — strict-inequality quadrants", () => {
  it("names the four quadrants and collapses zeros to coupled", () => {
    expect(classifyRegime(0.01, -0.01)).toBe("divergent");
    expect(classifyRegime(0.01, 0.01)).toBe("coupled");
    expect(classifyRegime(-0.01, -0.01)).toBe("risk-off");
    expect(classifyRegime(-0.01, 0.01)).toBe("credit-lead");
    expect(classifyRegime(0.01, 0)).toBe("coupled");
    expect(classifyRegime(0, -0.01)).toBe("coupled");
  });

  it("tones regimes with brand tokens", () => {
    expect(regimeColor("divergent")).toBe("var(--warning)");
    expect(regimeColor("coupled")).toBe("var(--positive)");
    expect(regimeColor("risk-off")).toBe("var(--negative)");
    expect(regimeColor("credit-lead")).toBe("var(--text-muted)");
    expect(regimeColor(null)).toBe("var(--text-muted)");
  });
});

describe("isNearHigh — 0.97 vs 0.98 on the fixture ratio", () => {
  const last = 7641.16015625;
  const high = 7798.990234375;

  it("is true at 0.97 and false at 0.98", () => {
    expect(last / high).toBeCloseTo(0.9797627547436404, 10);
    expect(isNearHigh(last, high, 0.97)).toBe(true);
    expect(isNearHigh(last, high, 0.98)).toBe(false);
    expect(NEAR_HIGH_RATIO).toBe(0.97);
    expect(isNearHigh(last, high)).toBe(true);
    expect(LOOKBACK_SESSIONS).toBe(168);
  });
});

/* ─── CreditSpreadPanel component ──────────────────────── */

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

const mockUseCreditSpread = vi.fn();
vi.mock("@/lib/useCreditSpread", () => ({
  useCreditSpread: (...args: unknown[]) => mockUseCreditSpread(...args),
}));

import CreditSpreadPanel from "../components/CreditSpreadPanel";

afterEach(() => {
  cleanup();
  mockUseCreditSpread.mockReset();
});

function point(overrides: Partial<CreditSpreadPoint> = {}): CreditSpreadPoint {
  return {
    date: "2026-08-20",
    hyg_close: 79.55999755859375,
    spx_close: 7641.16015625,
    ...overrides,
  };
}

function buildSeries(count: number): CreditSpreadPoint[] {
  return Array.from({ length: count }, (_, i) => {
    const day = new Date(Date.UTC(2024, 0, 2 + i));
    return point({
      date: day.toISOString().slice(0, 10),
      hyg_close: 77 + i * 0.01,
      spx_close: 4700 + i * 5,
    });
  });
}

function buildData(overrides: Partial<CreditSpreadData> = {}): CreditSpreadData {
  const series = buildSeries(40);
  return {
    scan_time: "2026-08-21T21:45:00Z",
    source: "yahoo",
    count: series.length,
    current: {
      date: "2026-08-20",
      hyg_close: 79.55999755859375,
      spx_close: 7641.16015625,
      hyg_ret: -0.013025716955806343,
      spx_ret: 0.12097839201868865,
      regime: "divergent",
      near_high: true,
    },
    series,
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: CreditSpreadData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as CreditSpreadData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseCreditSpread.mockReturnValue(state);
  return render(<CreditSpreadPanel />);
}

describe("CreditSpreadPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading high-yield credit series")).toBeTruthy();
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
        },
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No credit series yet")).toBeTruthy();
  });
});

describe("CreditSpreadPanel — header strip", () => {
  it("renders regime, HYG 8M, SPX 8M, and the latest session date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("credit-spread-regime-value").textContent).toBe("DIVERGENT");
    expect(screen.getByTestId("credit-spread-hyg-ret").textContent).toBe("-1.30%");
    expect(screen.getByTestId("credit-spread-spx-ret").textContent).toBe("+12.10%");
    expect(screen.getByTestId("credit-spread-session").textContent).toBe("20 Aug 2026");
  });

  it("tones a divergent regime as warning", () => {
    renderPanel(hookState({ data: buildData() }));
    const regime = screen.getByTestId("credit-spread-regime-value");
    expect(regime.style.color).toBe("var(--warning)");
  });
});

describe("CreditSpreadPanel — chart + controls", () => {
  it("renders the dual-axis chart title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("S&P 500 VS HYG")).toBeTruthy();
  });

  it("defaults the range to All on the multi-year daily series", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
  });

  it("renders the source footnote without ICE or cadence claims", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(
      screen.getByText(
        "Source: Interactive Brokers daily closes (HYG, S&P 500), then Unusual Whales, then Yahoo Finance. HYG is the traded high-yield credit proxy. ICE CCC OAS is not stored.",
      ),
    ).toBeTruthy();
  });

  it("does not emit NaN path coordinates when an SPX close is 0 on the log axis", () => {
    const data = buildData();
    data.series[0] = point({ ...data.series[0], spx_close: 0 });
    renderPanel(hookState({ data }));
    const paths = document.querySelectorAll("path");
    for (const path of Array.from(paths)) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

/**
 * REL-067 / R-197: the web twin of R-161. `classifyRegime` returned
 * `"coupled"` — a benign, tradeable label that `regimeColor` paints
 * `var(--positive)` — for a missing or non-finite return.
 */
describe("classifyRegime has no regime without returns", () => {
  it.each([
    [null, 0.01],
    [0.01, null],
    [undefined, 0.01],
    [Number.NaN, 0.01],
    [0.01, Number.POSITIVE_INFINITY],
  ])("returns null for (%s, %s)", (spx, hyg) => {
    expect(classifyRegime(spx as number | null, hyg as number | null)).toBeNull();
  });

  it("still classifies real returns", () => {
    expect(classifyRegime(0.02, -0.01)).toBe("divergent");
    expect(classifyRegime(0.02, 0.01)).toBe("coupled");
    expect(classifyRegime(-0.02, -0.01)).toBe("risk-off");
    expect(classifyRegime(-0.02, 0.01)).toBe("credit-lead");
  });

  it("paints an absent regime as muted, not positive", () => {
    expect(regimeColor(null)).toBe("var(--text-muted)");
    expect(regimeColor("coupled")).toBe("var(--positive)");
  });
});
