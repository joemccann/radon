/**
 * Cross-tab claim for the stale-snapshot auto-sync.
 *
 * The producer routes are rate-limited per USER (`portfolio-sync` and
 * `orders-refresh`, 4/min), so every tab and device of the operator draws
 * from one budget. A read-then-write cooldown in localStorage is not atomic:
 * Chrome aligns background-tab timers, so N tabs read the old `lastFiredAt`
 * together and all fire (2026-08-24: 364 five-way bursts, 963 x 429). The
 * Web Locks API gives one holder per origin, which makes read + write atomic
 * across tabs; where it is absent the claim degrades to the previous
 * synchronous path.
 */
export type AutoSyncState = { lastFiredAt: number; consecutive: number };

export interface ClaimStore {
  read(target: string): AutoSyncState | null;
  write(target: string, state: AutoSyncState): void;
}

export const AUTO_SYNC_COOLDOWN_MS = 60_000;
/** Cap the backoff so a producer that recovers is retried within ~16 min. */
export const MAX_BACKOFF_MULTIPLIER = 16;

const LOCK_PREFIX = "radon:autosync:";

export interface ClaimOptions {
  target: string;
  now: number;
  cooldownMs?: number;
  store: ClaimStore;
  locks: LockManager | undefined;
}

function claimUnderStore({ target, now, cooldownMs = AUTO_SYNC_COOLDOWN_MS, store }: ClaimOptions): boolean {
  const state = store.read(target) ?? { lastFiredAt: 0, consecutive: 0 };
  const multiplier = Math.min(MAX_BACKOFF_MULTIPLIER, 2 ** Math.max(0, state.consecutive));
  if (now - state.lastFiredAt < cooldownMs * multiplier) return false;
  store.write(target, { lastFiredAt: now, consecutive: state.consecutive + 1 });
  return true;
}

export interface ReleaseOptions {
  target: string;
  store: ClaimStore;
  locks: LockManager | undefined;
  /**
   * The record the caller observed before deciding to reset. Present only so
   * a stale read is visibly ignored — the reset re-reads under the lock and
   * never re-serialises this value.
   */
  seen?: AutoSyncState | null;
}

function releaseUnderStore({ target, store }: ReleaseOptions): void {
  // Re-READ inside the lock. The reset used to write back a `lastFiredAt`
  // captured before the lock, so a claim another tab took in between was
  // rewound to the pre-claim value with the backoff zeroed — and any tab with
  // an older or empty in-memory map then saw the cooldown as elapsed and
  // fired. That is the N-way burst the lock exists to eliminate. R-215.
  const state = store.read(target);
  if (!state || state.consecutive === 0) return;
  store.write(target, { lastFiredAt: state.lastFiredAt, consecutive: 0 });
}

/**
 * Clear the backoff ladder after the producer succeeds, atomically with
 * respect to concurrent claims. Never advances or rewinds `lastFiredAt`.
 */
export function releaseAutoSyncClaim(options: ReleaseOptions): void | Promise<void> {
  const { locks, target } = options;
  if (!locks) return releaseUnderStore(options);
  return locks
    .request(LOCK_PREFIX + target, async (lock) => {
      if (lock) releaseUnderStore(options);
    })
    .then(() => undefined)
    // Same degradation contract as the claim: a lock manager that refuses
    // must not strand the tab with a permanently escalated backoff.
    .catch(() => releaseUnderStore(options));
}

/**
 * Returns true for the single caller that may fire the sync now. Resolves
 * asynchronously under a Web Lock; returns synchronously without one.
 */
export function claimAutoSyncFire(options: ClaimOptions): boolean | Promise<boolean> {
  const { locks, target } = options;
  if (!locks) return claimUnderStore(options);
  return locks
    .request(LOCK_PREFIX + target, { ifAvailable: true }, async (lock) => {
      if (!lock) return false;
      return claimUnderStore(options);
    })
    .then((claimed: unknown) => claimed === true)
    // A lock manager that refuses (opaque origin, document not fully active)
    // must not strand the tab: degrade to the synchronous claim.
    .catch(() => claimUnderStore(options));
}
