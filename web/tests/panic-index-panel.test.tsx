/**
 * @vitest-environment jsdom
 *
 * Panic Proxy — four-leg Cboe composite (regime tab PANIC).
 *
 * Spec: docs/indicators/panic-index.md.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  DISCLAIMER,
  MISSING_PANIC_INDEX,
  RANK_MIN_ROWS,
  SOURCE_FOOTNOTE,
  deltaTone,
  formatClose,
  formatDelta,
  formatLevel,
  formatRank,
  formatTs,
  formatZ,
  type PanicIndexCurrent,
  type PanicIndexData,
  type PanicIndexPoint,
} from "@/lib/panicIndex";

describe("panicIndex formatters", () => {
  it("formats signed level, delta, and z", () => {
    expect(formatLevel(-0.6256)).toBe("-0.63");
    expect(formatDelta(-0.6269)).toBe("-0.63");
    expect(formatZ(-1.75)).toBe("-1.8");
    expect(formatZ(0.1573)).toBe("+0.2");
    expect(formatLevel(null)).toBe("---");
    expect(formatDelta(Number.NaN)).toBe("---");
  });

  it("formats rank and raw closes", () => {
    expect(formatRank(87, 2509)).toBe("#87 of 2509");
    expect(formatRank(1, RANK_MIN_ROWS - 1)).toBe("---");
    expect(formatClose(15.44)).toBe("15.44");
    expect(formatTs(0.8323)).toBe("0.8323");
  });

  it("freezes the exact HTTP-200 missing shape", () => {
    expect(MISSING_PANIC_INDEX).toEqual({
      missing: true,
      scan_time: null,
      source_last_modified: null,
      data_date: null,
      count: 0,
      delta_count: 0,
      current: null,
      stats: null,
      alert: null,
      series: [],
    });
    expect(Object.isFrozen(MISSING_PANIC_INDEX)).toBe(true);
  });
});

describe("deltaTone — strict inequalities, never positive", () => {
  const std = 0.36;
  it("stays muted at exactly 2σ and turns warning strictly beyond", () => {
    expect(deltaTone(2 * std, std)).toBe("var(--text-muted)");
    expect(deltaTone(-(2 * std), std)).toBe("var(--text-muted)");
    expect(deltaTone(2 * std + 0.001, std)).toBe("var(--warning)");
    expect(deltaTone(-(2 * std + 0.001), std)).toBe("var(--warning)");
  });

  it("turns negative strictly beyond 3σ", () => {
    expect(deltaTone(3 * std, std)).toBe("var(--warning)");
    expect(deltaTone(3 * std + 0.001, std)).toBe("var(--negative)");
    expect(deltaTone(-(3 * std + 0.001), std)).toBe("var(--negative)");
  });

  it("never uses var(--positive) and mutes nulls", () => {
    expect(deltaTone(0.1, std)).toBe("var(--text-muted)");
    expect(deltaTone(null, std)).toBe("var(--text-muted)");
    expect(deltaTone(-0.1, null)).toBe("var(--text-muted)");
    expect([deltaTone(0.1, std), deltaTone(-4, std), deltaTone(1, std)].join(" ")).not.toContain(
      "var(--positive)",
    );
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

const mockUsePanicIndex = vi.fn();
vi.mock("@/lib/usePanicIndex", () => ({
  usePanicIndex: (...args: unknown[]) => mockUsePanicIndex(...args),
}));

import PanicIndexPanel from "../components/PanicIndexPanel";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  mockUsePanicIndex.mockReset();
});

const SERIES_LENGTH = 600;
const DATA_DATE = "2026-09-17";

function daysAgo(n: number): string {
  const d = new Date("2026-09-17T20:00:00Z");
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

function buildSeries(length = SERIES_LENGTH): PanicIndexPoint[] {
  return Array.from({ length }, (_, i) => ({
    date: daysAgo(length - 1 - i),
    vix: 15 + 2 * Math.sin(i / 20),
    vix3m: 18 + Math.sin(i / 30),
    vvix: 90 + 5 * Math.sin(i / 25),
    ts: 0.83 + 0.05 * Math.sin(i / 22),
    skew: 145 + 2 * Math.sin(i / 18),
    z_vix: i < 251 ? null : 0.1 * Math.sin(i / 20),
    z_vvix: i < 251 ? null : 0.1 * Math.sin(i / 21),
    z_ts: i < 251 ? null : 0.1 * Math.sin(i / 19),
    z_skew: i < 251 ? null : 0.1 * Math.sin(i / 17),
    level: i < 251 ? null : 0.2 * Math.sin(i / 20),
    delta_1d: i < 252 ? null : 0.05 * Math.sin(i / 20),
  }));
}

function buildCurrent(overrides: Partial<PanicIndexCurrent> = {}): PanicIndexCurrent {
  return {
    date: DATA_DATE,
    level: -0.6256,
    delta_1d: -0.6269,
    delta_z: -1.75,
    delta_std_10y: 0.3574,
    rank_decline_10y: 87,
    rank_surge_10y: 2423,
    rank_n: 2509,
    legs: {
      vix: { value: 15.44, z: -0.8252 },
      vvix: { value: 87.72, z: -1.0661 },
      ts: { value: 0.8323, z: -0.7683, vix3m: 18.55 },
      skew: { value: 145.7, z: 0.1573 },
      skew25d: null,
    },
    ...overrides,
  };
}

function buildData(overrides: Partial<PanicIndexData> = {}): PanicIndexData {
  return {
    scan_time: "2026-09-18T02:50:00Z",
    source_last_modified: {
      vix: "Fri, 18 Sep 2026 01:51:00 GMT",
      vix3m: "Fri, 18 Sep 2026 01:51:00 GMT",
      vvix: "Fri, 18 Sep 2026 12:01:00 GMT",
      skew: "Fri, 18 Sep 2026 21:01:00 GMT",
    },
    data_date: DATA_DATE,
    count: SERIES_LENGTH,
    delta_count: SERIES_LENGTH - 252,
    current: buildCurrent(),
    stats: {
      high: 3.7143,
      high_date: "2018-02-05",
      low: -2.3411,
      low_date: "2024-08-06",
      avg: -0.0007,
      stddev: 0.3574,
    },
    alert: { last_fired_date: null, last_fired_kind: null },
    series: buildSeries(),
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: PanicIndexData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as PanicIndexData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUsePanicIndex.mockReturnValue(state);
  return render(<PanicIndexPanel />);
}

describe("PanicIndexPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading Cboe panic proxy series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the missing contract", () => {
    renderPanel(hookState({ data: { ...MISSING_PANIC_INDEX } }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No panic proxy reading yet")).toBeTruthy();
  });
});

describe("PanicIndexPanel — honesty + strip", () => {
  it("renders the disclaimer text exactly", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("panic-index-disclaimer").textContent).toBe(DISCLAIMER);
  });

  it("renders all eight strip values", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("panic-index-delta").textContent).toBe("-0.63");
    expect(screen.getByTestId("panic-index-level").textContent).toBe("-0.63");
    expect(screen.getByTestId("panic-index-rank").textContent).toBe("#87 of 2509");
    expect(screen.getByTestId("panic-index-vix").textContent).toBe("15.44");
    expect(screen.getByTestId("panic-index-vvix").textContent).toBe("87.72");
    expect(screen.getByTestId("panic-index-ts").textContent).toBe("0.8323");
    expect(screen.getByTestId("panic-index-skew").textContent).toBe("145.70");
    expect(screen.getByTestId("panic-index-source-updated").textContent).toBe(DATA_DATE);
  });

  it("switches the rank cell between decline, surge, and ---", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("panic-index-rank").textContent).toBe("#87 of 2509");
    cleanup();
    const surge = buildData();
    surge.current = buildCurrent({ delta_1d: 0.4, rank_surge_10y: 12 });
    renderPanel(hookState({ data: surge }));
    expect(screen.getByTestId("panic-index-rank").textContent).toBe("#12 of 2509");
    cleanup();
    const short = buildData();
    short.current = buildCurrent({ rank_n: RANK_MIN_ROWS - 1 });
    renderPanel(hookState({ data: short }));
    expect(screen.getByTestId("panic-index-rank").textContent).toBe("---");
  });

  it("shows the 25d overlay cell only when present", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.queryByTestId("panic-index-skew25d")).toBeNull();
    cleanup();
    const withOverlay = buildData();
    withOverlay.current = buildCurrent({
      legs: {
        ...buildCurrent().legs,
        skew25d: { value: 1.12, z: 0.4 },
      },
    });
    renderPanel(hookState({ data: withOverlay }));
    expect(screen.getByTestId("panic-index-skew25d").textContent).toBe("1.12");
  });
});

describe("PanicIndexPanel — chart + controls", () => {
  it("defaults to CHANGE and toggles the LEVEL title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("PANIC PROXY - 1D CHANGE")).toBeTruthy();
    fireEvent.click(screen.getByTestId("panic-index-view-level"));
    expect(screen.getByText("PANIC PROXY - LEVEL")).toBeTruthy();
  });

  it("defaults the range chips to All (no 10Y preset)", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
  });

  it("renders the brush minimap with the panic-index testid prefix", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("panic-index-brush")).toBeTruthy();
  });

  it("tolerates warm-up nulls without emitting NaN into path d", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

describe("PanicIndexPanel — copy discipline", () => {
  it("contains no em dashes and no asserted cadence copy", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const text = container.textContent ?? "";
    expect(text).not.toContain("—");
    expect(text).not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly|every)/i);
    expect(text).toContain(SOURCE_FOOTNOTE);
  });
});

describe("PanicIndexPanel — freshness rail", () => {
  it("renders as-of and a countdown to the 02:50 UTC slot", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-27T00:30:00Z"));
    renderPanel(hookState({ data: buildData({ data_date: "2026-08-26" }) }));
    act(() => {
      vi.advanceTimersByTime(0);
    });
    const rail = screen.getByTestId("panic-index-freshness-rail");
    expect(rail).toBeTruthy();
    expect(screen.getByTestId("panic-index-strip-asof").textContent).toContain("2026-08-26");
    expect(screen.getByTestId("panic-index-freshness-rail-countdown").textContent).toBe("2h 20m");
    expect(rail.textContent).toContain("Next sample");
  });
});
