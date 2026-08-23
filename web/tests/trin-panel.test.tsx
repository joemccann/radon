/**
 * @vitest-environment jsdom
 *
 * TRIN — NYSE Arms Index regime tab (60-minute bars, MA(10), low zone).
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  MISSING_TRIN,
  ZONE_HIGH,
  ZONE_LOW,
  ZONE_NEAR,
  formatTrin,
  stateLabel,
  stateTone,
  type TrinData,
  type TrinHourlyBar,
} from "@/lib/trin";

describe("trin helpers", () => {
  it("formats TRIN at two decimals and guards nulls", () => {
    expect(formatTrin(0.6123)).toBe("0.61");
    expect(formatTrin(null)).toBe("---");
    expect(formatTrin(Number.NaN)).toBe("---");
  });

  it("labels states without em dashes", () => {
    expect(stateLabel("in_zone")).toBe("IN ZONE");
    expect(stateLabel("near_zone")).toBe("NEAR ZONE");
    expect(stateLabel("elevated")).toBe("ELEVATED");
    expect(stateLabel("neutral")).toBe("NEUTRAL");
    expect(stateLabel(null)).toBe("---");
  });

  it("tones the low zone as a weakness warning", () => {
    expect(stateTone("in_zone")).toBe("negative");
    expect(stateTone("near_zone")).toBe("negative");
    expect(stateTone("elevated")).toBe("positive");
    expect(stateTone("neutral")).toBe("muted");
    expect(stateTone(null)).toBe("muted");
  });

  it("mirrors the zone thresholds and freezes the missing contract", () => {
    expect([ZONE_LOW, ZONE_NEAR, ZONE_HIGH]).toEqual([0.6, 0.65, 1.5]);
    expect(MISSING_TRIN).toEqual({ missing: true, scan_time: null, source: null, current: null, hourly: [], daily: [] });
    expect(Object.isFrozen(MISSING_TRIN)).toBe(true);
  });
});

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    class StubResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: typeof StubResizeObserver }).ResizeObserver = StubResizeObserver;
  }
});

const mockUseTrin = vi.fn();
vi.mock("@/lib/useTrin", () => ({ useTrin: (...args: unknown[]) => mockUseTrin(...args) }));

import TrinPanel from "../components/TrinPanel";

afterEach(() => {
  cleanup();
  mockUseTrin.mockReset();
});

function buildHourly(n: number): TrinHourlyBar[] {
  return Array.from({ length: n }, (_, i) => {
    const day = Math.floor(i / 7);
    const slot = i % 7;
    const stamp = new Date(Date.UTC(2026, 6, 6 + day, 14 + slot, 25));
    return {
      ts: stamp.toISOString(),
      bucket: `${stamp.toISOString().slice(0, 10)}T${String(9 + slot).padStart(2, "0")}:30`,
      trin: 0.8 + 0.3 * Math.sin(i / 3),
      ma10: i >= 9 ? 0.85 + 0.1 * Math.cos(i / 5) : null,
    };
  });
}

function buildData(overrides: Partial<TrinData> = {}): TrinData {
  const hourly = buildHourly(70);
  return {
    scan_time: "2026-08-21T19:55:00Z",
    source: "ib+stockcharts",
    current: {
      ts: hourly[hourly.length - 1].ts, session_date: "2026-08-21",
      trin: 0.68, ma10: 0.61, state: "near_zone",
      adv: 1510, dec: 1280, up_vol: 1.9e9, down_vol: 1.4e9,
      daily_close: 0.68, daily_date: "2026-08-21",
      zone_low: 0.6, zone_near: 0.65, zone_high: 1.5,
    },
    hourly,
    daily: [{ date: "2026-08-20", close: 0.77 }, { date: "2026-08-21", close: 0.68 }],
    ...overrides,
  };
}

function hookState(partial: Partial<{ data: TrinData | null; loading: boolean; syncing: boolean; error: string | null; lastSync: string | null }> = {}) {
  return { data: null as TrinData | null, loading: false, syncing: false, error: null as string | null, lastSync: null as string | null, syncNow: vi.fn(), ...partial };
}

describe("TrinPanel", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    mockUseTrin.mockReturnValue(hookState({ loading: true }));
    render(<TrinPanel />);
    expect(screen.getByText("Loading TRIN series")).toBeTruthy();
  });

  it("shows the empty state on the missing contract", () => {
    mockUseTrin.mockReturnValue(hookState({ data: { ...MISSING_TRIN } as unknown as TrinData }));
    render(<TrinPanel />);
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No TRIN samples yet")).toBeTruthy();
  });

  it("renders the strip, the chart title and the brush", () => {
    mockUseTrin.mockReturnValue(hookState({ data: buildData(), lastSync: "2026-08-21T19:55:00Z" }));
    render(<TrinPanel />);
    expect(screen.getByTestId("trin-value").textContent).toBe("0.68");
    expect(screen.getByTestId("trin-ma10").textContent).toBe("0.61");
    expect(screen.getByTestId("trin-state").textContent).toBe("NEAR ZONE");
    expect(screen.getByTestId("trin-daily").textContent).toContain("0.68");
    expect(screen.getByTestId("trin-advdec").textContent).toContain("1510");
    expect(screen.getByText("TRIN 60 MIN")).toBeTruthy();
    expect(screen.getByTestId("trin-brush")).toBeTruthy();
  });

  it("renders '---' for MA10 until ten bars exist", () => {
    const data = buildData();
    data.current = { ...data.current!, ma10: null, state: "neutral" };
    mockUseTrin.mockReturnValue(hookState({ data }));
    render(<TrinPanel />);
    expect(screen.getByTestId("trin-ma10").textContent).toBe("---");
    expect(screen.getByTestId("trin-state").textContent).toBe("NEUTRAL");
  });

  it("never emits NaN into chart paths across null MA10 bars", () => {
    mockUseTrin.mockReturnValue(hookState({ data: buildData() }));
    const { container } = render(<TrinPanel />);
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) expect(path.getAttribute("d")).not.toContain("NaN");
  });

  it("contains no cadence claims in its copy", () => {
    mockUseTrin.mockReturnValue(hookState({ data: buildData() }));
    const { container } = render(<TrinPanel />);
    expect(container.textContent ?? "").not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly|every)/i);
  });
});
