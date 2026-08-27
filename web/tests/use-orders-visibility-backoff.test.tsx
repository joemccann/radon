/**
 * @vitest-environment jsdom
 *
 * R-263 — `useOrders` visibility gate + failure backoff, observed.
 *
 * This lived in `p2-operability-remainder.test.ts` as three `toContain` greps
 * over the SOURCE TEXT of `lib/useOrders.ts`. Greps cannot observe either
 * mechanism: inverting the guard to `document.visibilityState !== "hidden"`
 * makes the hook skip the network while the tab is VISIBLE, so the open-orders
 * book on the live trading surface never refreshes, and all three greps stay
 * green. A backoff whose streak resets on every poll passes them too.
 *
 * So mount the hook with fake timers and assert on real fetch calls:
 *  - a hidden tab issues no poll, and the first tick after it returns does
 *  - three consecutive failures are spaced 60s / 120s / 240s, then capped
 *  - a hidden tick is neither a success nor a failure, so backgrounding does
 *    not walk the streak to the ceiling behind the operator's back
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useOrders } from "@/lib/useOrders";

const POLL_INTERVAL_MS = 30_000;

let visibility: DocumentVisibilityState = "visible";
let callTimes: number[] = [];

function setVisibility(next: DocumentVisibilityState): void {
  visibility = next;
}

/** Every `/api/orders` GET, stamped with the (faked) clock. */
function installFetch(status: number): void {
  global.fetch = vi.fn(async () => {
    callTimes.push(Date.now());
    return status === 200
      ? new Response(JSON.stringify({ orders: [], last_sync: "2026-08-27T14:00:00Z" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      : new Response(null, { status });
  }) as unknown as typeof fetch;
}

/** Advance the faked clock, flushing the awaited fetch microtasks with it. */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** Mount, let the unconditional initial load settle, then start counting. */
async function mountSettled() {
  const view = renderHook(() => useOrders(true));
  await advance(0);
  callTimes = [];
  return view;
}

beforeEach(() => {
  visibility = "visible";
  callTimes = [];
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => visibility,
  });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useOrders visibility gate", () => {
  it("issues no poll while the tab is hidden, and resumes on the next tick when it returns", async () => {
    installFetch(200);
    const { unmount } = await mountSettled();

    setVisibility("hidden");
    await advance(5 * 60_000);
    expect(callTimes).toHaveLength(0);

    setVisibility("visible");
    await advance(POLL_INTERVAL_MS);
    expect(callTimes).toHaveLength(1);

    unmount();
  });

  it("keeps polling a visible tab on the 30s cadence", async () => {
    installFetch(200);
    const { unmount } = await mountSettled();

    await advance(POLL_INTERVAL_MS);
    expect(callTimes).toHaveLength(1);
    await advance(POLL_INTERVAL_MS);
    expect(callTimes).toHaveLength(2);

    unmount();
  });
});

describe("useOrders failure backoff", () => {
  it("walks the ladder at 60s / 120s / 240s across consecutive failures", async () => {
    // The route answers a Turso outage with 503 by design.
    installFetch(503);
    const { unmount } = await mountSettled();

    // Four polls: the armed 30s tick, then each rung of the ladder.
    await advance(10 * 60_000);
    expect(callTimes.length).toBeGreaterThanOrEqual(4);

    const gaps = callTimes.slice(1, 4).map((t, i) => t - callTimes[i]);
    expect(gaps).toEqual([60_000, 120_000, 240_000]);

    unmount();
  });

  it("caps the ladder at the 5 minute ceiling rather than growing without bound", async () => {
    installFetch(503);
    const { unmount } = await mountSettled();

    await advance(40 * 60_000);
    const gaps = callTimes.slice(1).map((t, i) => t - callTimes[i]);
    expect(Math.max(...gaps)).toBe(5 * 60_000);
    // And it actually reaches the ceiling instead of stalling on one rung.
    expect(gaps).toContain(5 * 60_000);

    unmount();
  });

  it("does not count a hidden tick as a failure", async () => {
    installFetch(503);
    const { unmount } = await mountSettled();

    // One real failure: streak 1, so the next rung is 60s.
    await advance(POLL_INTERVAL_MS);
    expect(callTimes).toHaveLength(1);

    // Background the tab for five minutes. No request is issued, so none of
    // those ticks succeeded OR failed. Counting them walked the streak to the
    // ceiling, and the first failure after the tab returned waited 5 minutes
    // instead of the 120s rung.
    setVisibility("hidden");
    await advance(5 * 60_000);
    expect(callTimes).toHaveLength(1);

    setVisibility("visible");
    await advance(POLL_INTERVAL_MS);
    expect(callTimes).toHaveLength(2);

    // Second real failure -> rung two, 120s.
    await advance(120_000 - 1);
    expect(callTimes).toHaveLength(2);
    await advance(1);
    expect(callTimes).toHaveLength(3);

    unmount();
  });
});
