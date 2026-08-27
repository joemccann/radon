// @vitest-environment jsdom
/**
 * R-215 — the cross-tab claim is atomic only on the FIRE path.
 *
 * `claimUnderStore` does its read-modify-write of `lastFiredAt`/`consecutive`
 * under a Web Lock, but the RESET path did not: `useAutoSyncOnStale` called
 * `readState(target)` and then `writeState(target, {lastFiredAt:
 * current.lastFiredAt, consecutive: 0})` with no lock, re-serialising a
 * `lastFiredAt` it read earlier. If a second tab wins the lock and writes
 * `{lastFiredAt: now, consecutive: n+1}` in between, the resetting tab's
 * unlocked write REWINDS the shared `lastFiredAt` to the pre-claim value and
 * zeroes the backoff multiplier. Any tab whose in-memory map is older or empty
 * — a newly opened tab, a route remount — then reads the rewound record, sees
 * `now - lastFiredAt >= cooldownMs`, and fires immediately: the N-way burst
 * against the per-user 4/min budget the Web Lock exists to eliminate.
 */
import { describe, expect, it, vi } from "vitest";

import {
  AUTO_SYNC_COOLDOWN_MS,
  claimAutoSyncFire,
  releaseAutoSyncClaim,
  type AutoSyncState,
  type ClaimStore,
} from "../lib/autoSyncClaim";

type LockCallback = (lock: { name: string } | null) => Promise<unknown> | unknown;

/**
 * One holder per name. Models BOTH request shapes: `ifAvailable` callers get
 * null while held, and a two-argument blocking request waits its turn — the
 * reset uses the blocking form, and a fake that only knew `ifAvailable` would
 * send it down the catch-and-degrade path, passing for the wrong reason.
 */
function fakeLockManager() {
  const held = new Set<string>();
  const request = vi.fn(async (name: string, a: unknown, b?: unknown) => {
    const options = (typeof a === "function" ? {} : a) as { ifAvailable?: boolean };
    const cb = (typeof a === "function" ? a : b) as LockCallback;
    if (held.has(name)) {
      if (options.ifAvailable) return cb(null);
      while (held.has(name)) await Promise.resolve();
    }
    held.add(name);
    try {
      await Promise.resolve();
      return await cb({ name });
    } finally {
      held.delete(name);
    }
  });
  return { request } as unknown as LockManager & { request: typeof request };
}

function sharedStore(): { store: ClaimStore; shared: Map<string, AutoSyncState> } {
  const shared = new Map<string, AutoSyncState>();
  return {
    shared,
    store: {
      read: (target) => shared.get(target) ?? null,
      write: (target, state) => void shared.set(target, state),
    },
  };
}

const TARGET = "portfolio";

describe("releaseAutoSyncClaim", () => {
  it("clears the backoff without rewinding lastFiredAt", async () => {
    const { store, shared } = sharedStore();
    const locks = fakeLockManager();
    const now = 1_000_000;

    expect(await claimAutoSyncFire({ target: TARGET, now, store, locks })).toBe(true);
    await releaseAutoSyncClaim({ target: TARGET, store, locks });

    expect(shared.get(TARGET)).toEqual({ lastFiredAt: now, consecutive: 0 });
  });

  it("does not rewind a claim another tab took in between", async () => {
    // The interleaving the finding describes: tab A reads, tab B claims under
    // the lock, tab A writes back what it read.
    const { store, shared } = sharedStore();
    const locks = fakeLockManager();
    const firstFire = 1_000_000;

    await claimAutoSyncFire({ target: TARGET, now: firstFire, store, locks });
    const beforeSecond = shared.get(TARGET)!;

    // Tab B claims after the cooldown, advancing lastFiredAt.
    const secondFire = firstFire + AUTO_SYNC_COOLDOWN_MS * 4;
    expect(
      await claimAutoSyncFire({ target: TARGET, now: secondFire, store, locks }),
    ).toBe(true);

    // Tab A now resets from the stale view it read before tab B's claim.
    await releaseAutoSyncClaim({ target: TARGET, store, locks, seen: beforeSecond });

    const after = shared.get(TARGET)!;
    expect(after.lastFiredAt).toBe(secondFire);
    expect(after.lastFiredAt).not.toBe(beforeSecond.lastFiredAt);
  });

  it("leaves a third tab still inside the cooldown after a reset", async () => {
    const { store, shared } = sharedStore();
    const locks = fakeLockManager();
    const fired = 2_000_000;

    await claimAutoSyncFire({ target: TARGET, now: fired, store, locks });
    await releaseAutoSyncClaim({ target: TARGET, store, locks });

    // A newly opened tab reads the shared record a second later.
    expect(
      await claimAutoSyncFire({ target: TARGET, now: fired + 1_000, store, locks }),
    ).toBe(false);
    expect(shared.get(TARGET)!.lastFiredAt).toBe(fired);
  });

  it("takes the lock rather than degrading to the unlocked path", async () => {
    const { store } = sharedStore();
    const locks = fakeLockManager() as LockManager & { request: ReturnType<typeof vi.fn> };
    await claimAutoSyncFire({ target: TARGET, now: 1_000_000, store, locks });
    locks.request.mockClear();
    await releaseAutoSyncClaim({ target: TARGET, store, locks });
    expect(locks.request).toHaveBeenCalledWith(
      expect.stringContaining(TARGET),
      expect.any(Function),
    );
  });

  it("is a no-op when nothing has ever been claimed", async () => {
    const { store, shared } = sharedStore();
    await releaseAutoSyncClaim({ target: TARGET, store, locks: fakeLockManager() });
    expect(shared.has(TARGET)).toBe(false);
  });

  it("degrades synchronously where the Web Locks API is absent", async () => {
    const { store, shared } = sharedStore();
    const now = 3_000_000;
    await claimAutoSyncFire({ target: TARGET, now, store, locks: undefined });
    await releaseAutoSyncClaim({ target: TARGET, store, locks: undefined });
    expect(shared.get(TARGET)).toEqual({ lastFiredAt: now, consecutive: 0 });
  });
});
