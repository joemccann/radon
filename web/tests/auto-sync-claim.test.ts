// @vitest-environment jsdom
/**
 * 2026-08-24: 963 of the day's 1,345 HTTP 429s were POST /api/portfolio
 * (`portfolio-sync`, 4/min per user). Every open tab ran useAutoSyncOnStale
 * against a read-then-write localStorage cooldown; Chrome aligns background
 * tab timers, so five tabs read the old `lastFiredAt`, all wrote, and all
 * POSTed within 100 ms — the 5th got 429 and its "Too Many Requests" body
 * became the "Live data degraded" banner. The claim is now taken under a
 * Web Lock (one holder per origin), so the read-then-write is atomic across
 * tabs; the sync fallback keeps the previous behaviour where locks are absent.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import { claimAutoSyncFire, type ClaimStore } from "../lib/autoSyncClaim";
import { resetAutoSyncCooldowns, useAutoSyncOnStale } from "../lib/useAutoSyncOnStale";

type LockCallback = (lock: { name: string } | null) => Promise<unknown> | unknown;

/** Minimal LockManager: one holder per name, `ifAvailable` callers get null while held. */
function fakeLockManager() {
  const held = new Set<string>();
  const request = vi.fn(async (name: string, options: { ifAvailable?: boolean }, cb: LockCallback) => {
    if (held.has(name)) {
      if (options.ifAvailable) return cb(null);
      throw new Error("blocking request not modelled");
    }
    held.add(name);
    try {
      // Yield so concurrent requesters observe the lock as held.
      await Promise.resolve();
      return await cb({ name });
    } finally {
      held.delete(name);
    }
  });
  return { request } as unknown as LockManager & { request: typeof request };
}

/** One shared "localStorage" behind N per-tab stores with their own memory. */
function sharedStores(tabs: number): ClaimStore[] {
  const shared = new Map<string, { lastFiredAt: number; consecutive: number }>();
  return Array.from({ length: tabs }, () => {
    let memory: { lastFiredAt: number; consecutive: number } | null = null;
    return {
      read: (target: string) => {
        const stored = shared.get(target) ?? null;
        if (!memory) return stored;
        if (!stored) return memory;
        return stored.lastFiredAt > memory.lastFiredAt ? stored : memory;
      },
      write: (target: string, state: { lastFiredAt: number; consecutive: number }) => {
        memory = state;
        shared.set(target, state);
      },
    };
  });
}

describe("claimAutoSyncFire", () => {
  it("lets exactly one of N simultaneous tabs claim the fire under a lock", async () => {
    const locks = fakeLockManager();
    const stores = sharedStores(5);
    const now = 1_000_000;

    const results = await Promise.all(
      stores.map((store) => claimAutoSyncFire({ target: "portfolio", now, cooldownMs: 60_000, store, locks })),
    );

    expect(results.filter(Boolean)).toHaveLength(1);
    expect(locks.request).toHaveBeenCalledTimes(5);
  });

  it("refuses a tab that arrives after the winner wrote the cooldown", async () => {
    const locks = fakeLockManager();
    const [winner, late] = sharedStores(2);
    const now = 1_000_000;

    expect(await claimAutoSyncFire({ target: "portfolio", now, cooldownMs: 60_000, store: winner, locks })).toBe(true);
    expect(await claimAutoSyncFire({ target: "portfolio", now: now + 5_000, cooldownMs: 60_000, store: late, locks })).toBe(false);
    // The winner's fire has not cleared staleness yet, so the window doubled.
    expect(await claimAutoSyncFire({ target: "portfolio", now: now + 61_000, cooldownMs: 60_000, store: late, locks })).toBe(false);
    expect(await claimAutoSyncFire({ target: "portfolio", now: now + 121_000, cooldownMs: 60_000, store: late, locks })).toBe(true);
  });

  it("falls back to a synchronous claim when Web Locks are unavailable", () => {
    const [store] = sharedStores(1);
    const now = 1_000_000;
    const claimed = claimAutoSyncFire({ target: "orders", now, cooldownMs: 60_000, store, locks: undefined });
    expect(claimed).toBe(true);
    expect(claimAutoSyncFire({ target: "orders", now: now + 1_000, cooldownMs: 60_000, store, locks: undefined })).toBe(false);
  });

  it("doubles the window per consecutive fire that did not clear staleness", async () => {
    const locks = fakeLockManager();
    const [store] = sharedStores(1);
    store.write("portfolio", { lastFiredAt: 1_000_000, consecutive: 2 });

    expect(await claimAutoSyncFire({ target: "portfolio", now: 1_000_000 + 3 * 60_000, cooldownMs: 60_000, store, locks })).toBe(false);
    expect(await claimAutoSyncFire({ target: "portfolio", now: 1_000_000 + 4 * 60_000, cooldownMs: 60_000, store, locks })).toBe(true);
  });
});

describe("useAutoSyncOnStale with Web Locks present", () => {
  beforeEach(() => {
    resetAutoSyncCooldowns();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fires through the lock and still exactly once per cooldown", async () => {
    const locks = fakeLockManager();
    vi.stubGlobal("navigator", { ...navigator, locks });
    const syncNow = vi.fn();

    const { rerender } = renderHook(
      ({ tick }) => useAutoSyncOnStale(true, syncNow, "portfolio", true, tick),
      { initialProps: { tick: 0 } },
    );
    await waitFor(() => expect(syncNow).toHaveBeenCalledTimes(1));

    rerender({ tick: 1 });
    await waitFor(() => expect(locks.request).toHaveBeenCalledTimes(2));
    expect(syncNow).toHaveBeenCalledTimes(1);
  });
});

describe("claimAutoSyncFire degraded paths", () => {
  it("falls back to the synchronous claim when the lock manager rejects", async () => {
    const locks = { request: vi.fn(async () => { throw new Error("InvalidStateError"); }) } as unknown as LockManager;
    const [store] = sharedStores(1);
    expect(await claimAutoSyncFire({ target: "portfolio", now: 1_000_000, cooldownMs: 60_000, store, locks })).toBe(true);
    expect(await claimAutoSyncFire({ target: "portfolio", now: 1_001_000, cooldownMs: 60_000, store, locks })).toBe(false);
  });
});

describe("useAutoSyncOnStale ladder reset across tabs", () => {
  beforeEach(() => {
    resetAutoSyncCooldowns();
  });

  it("a tab that never won a claim still resets the shared ladder without rewinding lastFiredAt", () => {
    const firedAt = Date.now() - 5_000;
    window.localStorage.setItem("radon:autosync:portfolio", JSON.stringify({ lastFiredAt: firedAt, consecutive: 3 }));

    renderHook(() => useAutoSyncOnStale(false, vi.fn(), "portfolio", true));

    expect(JSON.parse(window.localStorage.getItem("radon:autosync:portfolio") ?? "{}")).toEqual({
      lastFiredAt: firedAt,
      consecutive: 0,
    });
  });
});
