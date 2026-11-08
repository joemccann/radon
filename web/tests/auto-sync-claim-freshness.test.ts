// @vitest-environment jsdom
/**
 * Audit 2026-11-03 §3.5 "Cross-tab auto-sync claim":
 *  1. a Web-Lock claim resolves after the effect that started it, and fired the
 *     `syncNow` captured at claim time, not the hook's current one;
 *  2. the merged cooldown record preferred this tab's memory on an EQUAL
 *     lastFiredAt, so another tab's reset of `consecutive` (which never moves
 *     lastFiredAt, R-215) was ignored and this tab kept the escalated backoff.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { resetAutoSyncCooldowns, useAutoSyncOnStale } from "../lib/useAutoSyncOnStale";

const T0 = new Date("2026-08-18T15:52:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  resetAutoSyncCooldowns();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function gatedLocks() {
  let open: () => void = () => {};
  const gate = new Promise<void>((resolve) => { open = resolve; });
  const locks = {
    request: (_name: string, optsOrCb: unknown, maybeCb?: unknown) => {
      const cb = (typeof optsOrCb === "function" ? optsOrCb : maybeCb) as (lock: unknown) => Promise<unknown>;
      return gate.then(() => cb({ name: _name }));
    },
  };
  return { locks, open: () => open() };
}

describe("auto-sync claim fires the current sync", () => {
  it("fires the latest syncNow when the lock resolves after a re-render", async () => {
    const { locks, open } = gatedLocks();
    vi.stubGlobal("navigator", { ...navigator, locks });
    const first = vi.fn();
    const latest = vi.fn();
    const { rerender } = renderHook(
      ({ syncNow }) => useAutoSyncOnStale(true, syncNow, "orders", true),
      { initialProps: { syncNow: first } },
    );
    rerender({ syncNow: latest });

    await act(async () => {
      open();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(first).not.toHaveBeenCalled();
    expect(latest).toHaveBeenCalledTimes(1);
  });
});

describe("auto-sync claim honours another tab's reset", () => {
  it("drops this tab's escalated backoff when another tab resets the ladder", () => {
    const syncNow = vi.fn();
    const { rerender } = renderHook(
      ({ tick }) => useAutoSyncOnStale(true, syncNow, "orders", true, tick),
      { initialProps: { tick: 0 } },
    );
    expect(syncNow).toHaveBeenCalledTimes(1);

    // Another tab's producer succeeded: it resets consecutive under the lock
    // and, by contract, never moves lastFiredAt.
    window.localStorage.setItem(
      "radon:autosync:orders",
      JSON.stringify({ lastFiredAt: T0.getTime(), consecutive: 0 }),
    );

    vi.advanceTimersByTime(61_000);
    rerender({ tick: 1 });
    expect(syncNow).toHaveBeenCalledTimes(2);
  });
});
