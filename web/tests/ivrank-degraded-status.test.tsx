/**
 * @vitest-environment jsdom
 *
 * R-304 (REL-104): the IV-rank panel can say "this failed".
 *
 * `fetch_ivrank` re-serves the PREVIOUS payload with a FRESH `scan_time` and
 * `status: "stale_source"` when both IB and UW are dead, and `degraded_uw`
 * when only UW answered. The route passes both through untouched, and the
 * panel never read `status` at all — so a both-feeds-down render was
 * pixel-identical to a healthy one, with a just-now clock sitting over a
 * reading that had not moved.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { IvRankData, IvRankEntry } from "@/lib/ivrank";
import IvRankPanel from "@/components/IvRankPanel";

// jsdom ships no ResizeObserver; the d3 chart wires one up on mount.
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

const mockUseIvRank = vi.fn();
vi.mock("@/lib/useIvRank", () => ({
  useIvRank: (...args: unknown[]) => mockUseIvRank(...args),
}));

afterEach(() => {
  cleanup();
  mockUseIvRank.mockReset();
});

const DAY_MS = 86_400_000;
const SERIES_END_MS = Date.UTC(2026, 7, 21);

function buildSeries(length = 90): IvRankEntry[] {
  return Array.from({ length }, (_, i) => ({
    date: new Date(SERIES_END_MS - (length - 1 - i) * DAY_MS).toISOString().slice(0, 10),
    iv: 0.12 + 0.0002 * i,
    iv_rank: i < 10 ? null : 10 + (i % 50),
    iv_pct: i < 10 ? null : 12 + (i % 40),
  }));
}

function buildData(overrides: Partial<IvRankData> = {}): IvRankData {
  return {
    // A FRESH restamp — this is exactly what the stale path writes.
    scan_time: new Date().toISOString(),
    status: "ok",
    source: "ib",
    as_of: "2026-08-21",
    expected_session: "2026-08-21",
    market_status: "closed",
    rank_window: 252,
    count: 90,
    rank_count: 80,
    current: { date: "2026-08-21", iv: 0.12201147, iv_rank: 10.559822, iv_pct: 12.2, regime: "SUPPRESSED", iv_1y_low: 0.105, iv_1y_high: 0.263 },
    series: buildSeries(),
    stats: null,
    uw_check: null,
    ...overrides,
  } as unknown as IvRankData;
}

function hookState(over: Record<string, unknown> = {}) {
  return { data: buildData(), loading: false, error: null, refresh: vi.fn(), ...over };
}

describe("a stale_source payload is not presented as current", () => {
  it("renders a degraded affordance", () => {
    mockUseIvRank.mockReturnValue(
      hookState({ data: buildData({ status: "stale_source" } as never) }),
    );
    render(<IvRankPanel />);
    expect(screen.getByTestId("ivrank-degraded")).toBeTruthy();
  });

  it("dates the header off the data, not off the restamp", () => {
    mockUseIvRank.mockReturnValue(
      hookState({ data: buildData({ status: "stale_source" } as never) }),
    );
    const { container } = render(<IvRankPanel />);
    // `as_of` is 2026-08-21; `scan_time` is now. The header must not present
    // the restamp as the age of the reading.
    const header = container.querySelector(".section-header") as HTMLElement;
    expect(header).toBeTruthy();
    expect(header.textContent).toContain("2026-08-21");

    const nowClock = new Date().toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
    });
    expect(header.textContent).not.toContain(nowClock);
  });

  it("still shows the live clock on a healthy payload", () => {
    mockUseIvRank.mockReturnValue(hookState());
    const { container } = render(<IvRankPanel />);
    const header = container.querySelector(".section-header") as HTMLElement;
    const nowClock = new Date().toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
    });
    expect(header.textContent).toContain(nowClock);
  });

  it("marks a degraded_uw payload too", () => {
    mockUseIvRank.mockReturnValue(
      hookState({ data: buildData({ status: "degraded_uw" } as never) }),
    );
    render(<IvRankPanel />);
    expect(screen.getByTestId("ivrank-degraded")).toBeTruthy();
  });

  it("leaves a healthy payload untouched", () => {
    mockUseIvRank.mockReturnValue(hookState());
    render(<IvRankPanel />);
    expect(screen.queryByTestId("ivrank-degraded")).toBeNull();
  });
});
