/**
 * @vitest-environment jsdom
 *
 * IEI/HYG ratio — IEI/HYG regime tab.
 *
 * Pure helpers (lib/ieiHyg.ts): ratio formatting, state labels and tones.
 * IeiHygPanel: gating (SpectralLoader / SectionEmptyState), the header stat
 * strip, the chart title, the brush, a null-DXY gap, and a NaN guard.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  MISSING_IEI_HYG,
  formatRatio,
  stateLabel,
  stateTone,
  type IeiHygData,
  type IeiHygPoint,
} from "@/lib/ieiHyg";

describe("formatRatio / stateLabel / stateTone", () => {
  it("formats the ratio at four decimals and guards nulls", () => {
    expect(formatRatio(1.462253520532856)).toBe("1.4623");
    expect(formatRatio(null)).toBe("---");
    expect(formatRatio(Number.NaN)).toBe("---");
  });

  it("labels states without em dashes", () => {
    expect(stateLabel("new_low")).toBe("NEW 52W LOW");
    expect(stateLabel("new_high")).toBe("NEW 52W HIGH");
    expect(stateLabel("neutral")).toBe("NEUTRAL");
    expect(stateLabel(null)).toBe("---");
  });

  it("tones a new low as risk-on (positive) and a new high as negative", () => {
    expect(stateTone("new_low")).toBe("positive");
    expect(stateTone("new_high")).toBe("negative");
    expect(stateTone("neutral")).toBe("muted");
    expect(stateTone(null)).toBe("muted");
  });

  it("freezes the missing contract", () => {
    expect(MISSING_IEI_HYG).toEqual({
      missing: true,
      scan_time: null,
      source: null,
      count: 0,
      current: null,
      series: [],
    });
    expect(Object.isFrozen(MISSING_IEI_HYG)).toBe(true);
  });
});

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

const mockUseIeiHyg = vi.fn();
vi.mock("@/lib/useIeiHyg", () => ({
  useIeiHyg: (...args: unknown[]) => mockUseIeiHyg(...args),
}));

import IeiHygPanel from "../components/IeiHygPanel";

afterEach(() => {
  cleanup();
  mockUseIeiHyg.mockReset();
});

function buildSeries(n: number): IeiHygPoint[] {
  return Array.from({ length: n }, (_, i) => {
    const day = new Date(Date.UTC(2026, 4, 26 + i));
    const iei = 117 - i * 0.01;
    const hyg = 80 + i * 0.005;
    return {
      date: day.toISOString().slice(0, 10),
      iei_close: iei,
      hyg_close: hyg,
      dxy_close: i % 7 === 3 ? null : 99 - i * 0.005,
      ratio: iei / hyg,
    };
  });
}

function buildData(overrides: Partial<IeiHygData> = {}): IeiHygData {
  const series = buildSeries(62);
  return {
    scan_time: "2026-08-22T21:55:00Z",
    source: "yahoo",
    count: series.length,
    current: {
      date: "2026-08-21",
      iei_close: 116.41000366210938,
      hyg_close: 79.61000061035156,
      dxy_close: 98.80000305175781,
      ratio: 1.462253520532856,
      ratio_52w_low: 1.462253520532856,
      low_date: "2026-08-21",
      ratio_52w_high: 1.475760927676247,
      high_date: "2026-06-26",
      ratio_pct_rank: 0,
      window_sessions: 62,
      state: "new_low",
    },
    series,
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: IeiHygData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as IeiHygData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel() {
  return render(<IeiHygPanel />);
}

describe("IeiHygPanel", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    mockUseIeiHyg.mockReturnValue(hookState({ loading: true }));
    renderPanel();
    expect(screen.getByText("Loading IEI/HYG series")).toBeTruthy();
  });

  it("shows the empty state on the missing contract", () => {
    mockUseIeiHyg.mockReturnValue(hookState({ data: { ...MISSING_IEI_HYG } as unknown as IeiHygData }));
    renderPanel();
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No IEI/HYG snapshot")).toBeTruthy();
  });

  it("renders the strip from current and the chart title", () => {
    mockUseIeiHyg.mockReturnValue(hookState({ data: buildData(), lastSync: "2026-08-22T21:55:00Z" }));
    renderPanel();
    expect(screen.getByTestId("iei-hyg-ratio").textContent).toBe("1.4623");
    expect(screen.getByTestId("iei-hyg-state").textContent).toBe("NEW 52W LOW");
    expect(screen.getByTestId("iei-hyg-low").textContent).toContain("1.4623");
    expect(screen.getByTestId("iei-hyg-high").textContent).toContain("1.4758");
    expect(screen.getByTestId("iei-hyg-rank").textContent).toBe("0%");
    expect(screen.getByTestId("iei-hyg-dxy").textContent).toBe("98.80");
    expect(screen.getByText("IEI / HYG RATIO")).toBeTruthy();
    expect(screen.getByTestId("iei-hyg-brush")).toBeTruthy();
  });

  it("never emits NaN into chart paths, including across null DXY gaps", () => {
    mockUseIeiHyg.mockReturnValue(hookState({ data: buildData() }));
    const { container } = renderPanel();
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d")).not.toContain("NaN");
    }
  });

  it("contains no cadence claims in its copy", () => {
    mockUseIeiHyg.mockReturnValue(hookState({ data: buildData() }));
    const { container } = renderPanel();
    expect(container.textContent ?? "").not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly)/i);
  });
});
