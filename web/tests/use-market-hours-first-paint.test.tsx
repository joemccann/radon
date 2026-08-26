/**
 * @vitest-environment jsdom
 *
 * T-114 — the first-paint placeholder, asserted where it is observable.
 *
 * The single previous case read `result.current` after `renderHook`. RTL wraps
 * render in `act()`, so the mount effect (`useMarketHours.ts:47-49`, which
 * calls `check()` immediately) has already corrected the state by the time the
 * assertion runs. Replacing the lazy initializer with
 * `useState<MarketState>(MarketState.CLOSED)` — restoring the exact CLOSED
 * placeholder flash the file exists to forbid — still reported 1 passed.
 *
 * `renderToStaticMarkup` never runs effects, so what it emits IS the first
 * paint. The pure `marketStateAt` cases then pin the boundaries the hook reads.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarketState, marketStateAt, useMarketHours } from "../lib/useMarketHours";

function MarketStateProbe() {
  return <span data-testid="market-state">{useMarketHours()}</span>;
}

describe("useMarketHours first paint", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits OPEN in the server-rendered markup, before any effect runs", () => {
    vi.useFakeTimers();
    // Monday 2026-08-17 11:00 ET
    vi.setSystemTime(new Date("2026-08-17T15:00:00Z"));

    const html = renderToStaticMarkup(<MarketStateProbe />);

    expect(html).toContain(`>${MarketState.OPEN}<`);
    expect(html).not.toContain(`>${MarketState.CLOSED}<`);
  });

  it("emits CLOSED in the first paint when the market really is closed", () => {
    vi.useFakeTimers();
    // Saturday 2026-08-22 12:00 ET
    vi.setSystemTime(new Date("2026-08-22T16:00:00Z"));

    expect(renderToStaticMarkup(<MarketStateProbe />)).toContain(`>${MarketState.CLOSED}<`);
  });
});

describe("marketStateAt boundaries", () => {
  // August 2026 is EDT (UTC-4). Monday 2026-08-17 unless noted.
  const CASES: Array<[string, string, MarketState]> = [
    ["03:59 ET — before the extended session", "2026-08-17T07:59:00Z", MarketState.CLOSED],
    ["04:00 ET — extended session opens", "2026-08-17T08:00:00Z", MarketState.EXTENDED],
    ["09:29 ET — one minute before the bell", "2026-08-17T13:29:00Z", MarketState.EXTENDED],
    ["09:30 ET — the bell", "2026-08-17T13:30:00Z", MarketState.OPEN],
    ["16:00 ET — the close, inclusive", "2026-08-17T20:00:00Z", MarketState.OPEN],
    ["16:01 ET — after the close", "2026-08-17T20:01:00Z", MarketState.EXTENDED],
    ["20:00 ET — extended session ends, inclusive", "2026-08-18T00:00:00Z", MarketState.EXTENDED],
    ["20:01 ET — overnight", "2026-08-18T00:01:00Z", MarketState.CLOSED],
    ["Saturday RTH clock", "2026-08-22T15:00:00Z", MarketState.CLOSED],
    ["Sunday RTH clock", "2026-08-23T15:00:00Z", MarketState.CLOSED],
  ];

  it.each(CASES)("%s", (_label, instant, expected) => {
    expect(marketStateAt(new Date(instant))).toBe(expected);
  });
});
