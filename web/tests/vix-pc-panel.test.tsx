/**
 * @vitest-environment jsdom
 *
 * VIX P/C regime tab — 5-session cumulative VIX put/call ratio.
 *
 * Spec: docs/indicators/vix-pc.md sections C.4, G.2.
 */
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  BAND_PCTL_HIGH,
  BAND_PCTL_LOW,
  WINDOW,
  formatPctile,
  formatRatio,
  vixPcRegime,
  vixPcRegimeColor,
  type VixPcData,
  type VixPcEntry,
} from "@/lib/vixPc";

describe("formatRatio — three-decimal ratio", () => {
  it("formats the calibration cum5", () => {
    expect(formatRatio(0.3188487844029556)).toBe("0.319");
    expect(formatRatio(0.2417297884911582)).toBe("0.242");
    expect(formatRatio(0)).toBe("0.000");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatRatio(null)).toBe("---");
    expect(formatRatio(undefined)).toBe("---");
    expect(formatRatio(Number.NaN)).toBe("---");
  });
});

describe("formatPctile — one-decimal percentile", () => {
  it("formats the calibration percentile", () => {
    expect(formatPctile(4.032258064516129)).toBe("4.0");
    expect(formatPctile(100)).toBe("100.0");
  });

  it("returns --- for null and non-finite", () => {
    expect(formatPctile(null)).toBe("---");
    expect(formatPctile(Number.NaN)).toBe("---");
  });
});

describe("vixPcRegime — strict band boundaries (spec C.4)", () => {
  it("classifies the interior of each band", () => {
    expect(vixPcRegime(4.0)).toBe("CAUTIOUS");
    expect(vixPcRegime(50)).toBe("NORMAL");
    expect(vixPcRegime(95)).toBe("COMPLACENT");
  });

  it("boundary values belong to the band above", () => {
    expect(vixPcRegime(9.999)).toBe("CAUTIOUS");
    expect(vixPcRegime(10)).toBe("NORMAL");
    expect(vixPcRegime(89.999)).toBe("NORMAL");
    expect(vixPcRegime(90)).toBe("COMPLACENT");
  });

  it("null and non-finite have no regime", () => {
    expect(vixPcRegime(null)).toBeNull();
    expect(vixPcRegime(Number.NaN)).toBeNull();
  });
});

describe("vixPcRegimeColor — brand tokens only", () => {
  it("maps bands to tokens, COMPLACENT to dislocation not negative", () => {
    expect(vixPcRegimeColor("CAUTIOUS")).toBe("var(--warning)");
    expect(vixPcRegimeColor("NORMAL")).toBe("var(--text-muted)");
    expect(vixPcRegimeColor("COMPLACENT")).toBe("var(--dislocation)");
    expect(vixPcRegimeColor(null)).toBe("var(--text-muted)");
  });
});

describe("display constants", () => {
  it("mirrors the job window and percentile bands", () => {
    expect(WINDOW).toBe(5);
    expect(BAND_PCTL_LOW).toBe(10);
    expect(BAND_PCTL_HIGH).toBe(90);
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

const mockUseVixPc = vi.fn();
vi.mock("@/lib/useVixPc", () => ({
  useVixPc: (...args: unknown[]) => mockUseVixPc(...args),
}));

import VixPcPanel from "../components/VixPcPanel";

afterEach(() => {
  cleanup();
  mockUseVixPc.mockReset();
  vi.useRealTimers();
});

const DAY_MS = 86_400_000;
const SERIES_LENGTH = 90;
const SERIES_END_MS = Date.UTC(2026, 9, 16);

function seriesDate(index: number): string {
  return new Date(SERIES_END_MS - (SERIES_LENGTH - 1 - index) * DAY_MS)
    .toISOString()
    .slice(0, 10);
}

function buildSeries(length = SERIES_LENGTH): VixPcEntry[] {
  return Array.from({ length }, (_, i) => ({
    date: seriesDate(i),
    put_volume: 100_000 + i,
    call_volume: 300_000 + i,
    daily_ratio: i < 4 ? 0.4 : 0.33 + (i % 20) * 0.01,
    cum5_ratio: i < 4 ? null : 0.30 + (i % 15) * 0.01,
  }));
}

function buildData(overrides: Partial<VixPcData> = {}): VixPcData {
  return {
    scan_time: new Date(SERIES_END_MS).toISOString(),
    status: "ok",
    source: "uw",
    as_of: "2026-10-16",
    data_date: "2026-10-16",
    expected_session: "2026-10-16",
    market_status: "closed",
    window: 5,
    count: SERIES_LENGTH,
    cum5_count: SERIES_LENGTH - 4,
    current: {
      date: "2026-10-16",
      put_volume: 573686,
      call_volume: 1729988,
      daily_ratio: 0.33161270482801036,
      cum5_ratio: 0.3188487844029556,
      percentile: 4.032258064516129,
      regime: "CAUTIOUS",
      series_min: 0.2417297884911582,
      series_max: 1.6973206456466932,
      series_p10: 0.3435408949777816,
      series_p90: 0.6760256318262985,
      series_min_date: "2026-10-08",
      is_series_low: false,
    },
    stats: {
      min: 0.2417297884911582,
      p10: 0.3435408949777816,
      p25: 0.3943868581804748,
      median: 0.48050480250544647,
      p75: 0.5889207941175402,
      p90: 0.6760256318262985,
      max: 1.6973206456466932,
      mean: 0.5057092921999375,
    },
    series: buildSeries(),
    ...overrides,
  } as VixPcData;
}

type HookState = {
  data: VixPcData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: Date | null;
  refresh: () => void;
};

function hookState(overrides: Partial<HookState> = {}): HookState {
  return {
    data: buildData(),
    loading: false,
    syncing: false,
    error: null,
    lastSync: new Date(SERIES_END_MS),
    refresh: vi.fn(),
    ...overrides,
  };
}

describe("VixPcPanel — gating", () => {
  it("renders the SpectralLoader with its label while loading with no data", () => {
    mockUseVixPc.mockReturnValue(hookState({ data: null, loading: true }));
    render(<VixPcPanel />);
    expect(screen.getByText(/Loading VIX put\/call series/i)).toBeTruthy();
  });

  it("renders the empty state on missing:true", () => {
    mockUseVixPc.mockReturnValue(
      hookState({
        data: {
          missing: true,
          status: "missing",
          scan_time: null,
          as_of: null,
          data_date: null,
          count: 0,
          series: [],
          current: null,
          stats: null,
        } as unknown as VixPcData,
      }),
    );
    render(<VixPcPanel />);
    expect(screen.getByText(/vix-pc refresh timer/i)).toBeTruthy();
  });
});

describe("VixPcPanel — summary strip", () => {
  it("renders the calibration strip values", () => {
    mockUseVixPc.mockReturnValue(hookState());
    render(<VixPcPanel />);
    expect(screen.getByTestId("vix-pc-cum5").textContent).toBe("0.319");
    expect(screen.getByTestId("vix-pc-percentile").textContent).toBe("4.0");
    expect(screen.getByTestId("vix-pc-regime").textContent).toBe("CAUTIOUS");
    expect(screen.getByTestId("vix-pc-daily").textContent).toBe("0.332");
    expect(screen.getByTestId("vix-pc-series-low").textContent).toMatch(/0\.242/);
    expect(screen.getByTestId("vix-pc-series-low").textContent).toMatch(/2026-10-08/);
  });

  it("renders --- for a null-cum5 current (pre-window history)", () => {
    mockUseVixPc.mockReturnValue(
      hookState({
        data: buildData({
          current: {
            date: "2026-10-16",
            put_volume: 10,
            call_volume: 20,
            daily_ratio: 0.5,
            cum5_ratio: null,
            percentile: null,
            regime: null,
            series_min: null,
            series_max: null,
            series_p10: null,
            series_p90: null,
            series_min_date: null,
            is_series_low: false,
          },
        }),
      }),
    );
    render(<VixPcPanel />);
    expect(screen.getAllByText("---").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("CAUTIOUS")).toBeNull();
  });
});

describe("VixPcPanel — chart", () => {
  it("renders the chart title", () => {
    mockUseVixPc.mockReturnValue(hookState());
    render(<VixPcPanel />);
    expect(screen.getByText("VIX 5S PUT/CALL")).toBeTruthy();
  });

  it("emits no NaN into any svg path (null cum5 rows are gaps, not zeros)", () => {
    mockUseVixPc.mockReturnValue(hookState());
    const { container } = render(<VixPcPanel />);
    const paths = Array.from(container.querySelectorAll("path"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

describe("VixPcPanel — freshness rail", () => {
  it("shows the session date and counts down to the 02:00 UTC slot", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-18T00:30:00Z"));
    mockUseVixPc.mockReturnValue(hookState());
    render(<VixPcPanel />);
    act(() => {
      vi.advanceTimersByTime(0);
    });
    const rail = screen.getByTestId("vix-pc-freshness-rail");
    expect(rail).toBeTruthy();
    expect(screen.getByTestId("vix-pc-strip-asof").textContent).toContain("2026-10-16");
    expect(screen.getByTestId("vix-pc-freshness-rail-countdown").textContent).toBe("1h 30m");
    expect(rail.textContent).toContain("Next sample");
  });
});

describe("VixPcPanel — copy discipline", () => {
  it("makes no predictive claim and hardcodes no cadence copy", () => {
    mockUseVixPc.mockReturnValue(hookState());
    const { container } = render(<VixPcPanel />);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/will rise|will fall|predicts|forecast/i);
    expect(text).not.toMatch(/refreshes (daily|hourly|5m)/i);
    expect(text).not.toContain("—");
  });
});
