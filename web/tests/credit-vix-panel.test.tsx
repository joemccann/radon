/**
 * @vitest-environment jsdom
 *
 * CREDIT/VIX — SHY minus HYG vs VIX regime tab.
 */
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  MISSING_CREDIT_VIX,
  formatGap,
  formatSpread,
  stateLabel,
  stateTone,
  type CreditVixData,
  type CreditVixPoint,
} from "@/lib/creditVix";

describe("formatSpread / stateLabel / stateTone", () => {
  it("formats the spread at two decimals and guards nulls", () => {
    expect(formatSpread(3.8000030517578125)).toBe("3.80");
    expect(formatSpread(null)).toBe("---");
    expect(formatSpread(Number.NaN)).toBe("---");
    expect(formatGap(0.854)).toBe("0.85");
  });

  it("labels states without em dashes", () => {
    expect(stateLabel("CREDIT WIDE")).toBe("CREDIT WIDE");
    expect(stateLabel("VIX WIDE")).toBe("VIX WIDE");
    expect(stateLabel("ALIGNED")).toBe("ALIGNED");
    expect(stateLabel(null)).toBe("---");
  });

  it("tones CREDIT WIDE as negative and VIX WIDE as positive", () => {
    expect(stateTone("CREDIT WIDE")).toBe("negative");
    expect(stateTone("VIX WIDE")).toBe("positive");
    expect(stateTone("ALIGNED")).toBe("muted");
    expect(stateTone(null)).toBe("muted");
  });

  it("freezes the missing contract", () => {
    expect(MISSING_CREDIT_VIX).toEqual({
      missing: true,
      scan_time: null,
      source: null,
      count: 0,
      current: null,
      series: [],
    });
    expect(Object.isFrozen(MISSING_CREDIT_VIX)).toBe(true);
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

const mockUseCreditVix = vi.fn();
vi.mock("@/lib/useCreditVix", () => ({
  useCreditVix: (...args: unknown[]) => mockUseCreditVix(...args),
}));

import CreditVixPanel from "../components/CreditVixPanel";

afterEach(() => {
  cleanup();
  mockUseCreditVix.mockReset();
});

function buildSeries(n: number): CreditVixPoint[] {
  return Array.from({ length: n }, (_, i) => {
    const day = new Date(Date.UTC(2026, 4, 26 + i));
    const shy = 80 + i * 0.01;
    const hyg = 77.5 - i * 0.005;
    return {
      date: day.toISOString().slice(0, 10),
      shy_close: shy,
      hyg_close: hyg,
      vix_close: 16 + (i % 5) * 0.2,
      spread: shy - hyg,
    };
  });
}

function buildData(overrides: Partial<CreditVixData> = {}): CreditVixData {
  const series = buildSeries(88);
  return {
    scan_time: "2026-09-30T22:25:00Z",
    source: "yahoo",
    count: series.length,
    current: {
      date: "2026-09-29",
      shy_close: 81.16000366210938,
      hyg_close: 77.36000061035156,
      vix_close: 16.040000915527344,
      spread: 3.8000030517578125,
      rank_spread: 1,
      rank_vix: 0.146,
      gap: 0.854,
      state: "CREDIT WIDE",
      widest_since: "2015-08-17",
      window_sessions: 88,
    },
    series,
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: CreditVixData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as CreditVixData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel() {
  return render(<CreditVixPanel />);
}

describe("CreditVixPanel", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    mockUseCreditVix.mockReturnValue(hookState({ loading: true }));
    renderPanel();
    expect(screen.getByText("Loading SHY minus HYG vs VIX series")).toBeTruthy();
  });

  it("shows the empty state on the missing contract", () => {
    mockUseCreditVix.mockReturnValue(hookState({ data: { ...MISSING_CREDIT_VIX } as unknown as CreditVixData }));
    renderPanel();
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No SHY minus HYG vs VIX snapshot")).toBeTruthy();
  });

  it("renders the strip from current and the chart title", () => {
    mockUseCreditVix.mockReturnValue(hookState({ data: buildData(), lastSync: "2026-09-30T22:25:00Z" }));
    renderPanel();
    expect(screen.getByTestId("credit-vix-spread").textContent).toBe("3.80");
    expect(screen.getByTestId("credit-vix-vix").textContent).toBe("16.04");
    expect(screen.getByTestId("credit-vix-gap").textContent).toBe("0.85");
    expect(screen.getByTestId("credit-vix-state").textContent).toBe("CREDIT WIDE");
    expect(screen.getByTestId("credit-vix-widest-since").textContent).toContain("2015");
    expect(screen.getAllByText("SHY MINUS HYG VS VIX").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("credit-vix-brush")).toBeTruthy();
  });

  it("never emits NaN into chart paths", () => {
    mockUseCreditVix.mockReturnValue(hookState({ data: buildData() }));
    const { container } = renderPanel();
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d")).not.toContain("NaN");
    }
  });

  it("contains no cadence claims and no front-run copy", () => {
    mockUseCreditVix.mockReturnValue(hookState({ data: buildData() }));
    const { container } = renderPanel();
    expect(container.textContent ?? "").not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly)/i);
    expect(container.textContent ?? "").not.toMatch(/front-run/i);
    expect(container.textContent ?? "").not.toContain("—");
  });
});

describe("CreditVixPanel — freshness rail", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the raw session date and counts down to the 22:25 UTC slot", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-30T21:00:00Z"));
    mockUseCreditVix.mockReturnValue(
      hookState({
        data: buildData({
          scan_time: "2026-09-29T22:25:00Z",
          current: { ...buildData().current!, date: "2026-09-29" },
        }),
      }),
    );
    renderPanel();
    act(() => {
      vi.advanceTimersByTime(0);
    });
    const rail = screen.getByTestId("credit-vix-freshness-rail");
    expect(rail).toBeTruthy();
    expect(rail.textContent).toContain("2026-09-29");
    expect(screen.getByTestId("credit-vix-freshness-rail-countdown").textContent).toBe("1h 25m");
    expect(rail.textContent).toContain("Next sample");
  });
});
