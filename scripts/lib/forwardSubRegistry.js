/**
 * Refcount registry for the WS relay's INTERNAL forward subscriptions.
 *
 * A forward-priced index option (VIX) needs an L1 on the FUTURE of its own
 * expiry. The relay opens that line under an internal key ("@fwd:VIX",
 * "@fwd:VIX:20260916") that no client ever names, so nothing torn down by
 * unsubscribeClientFromSymbol / disconnectClient — both walk client-facing keys
 * only — ever reached it. Result: one leaked IB market-data line (plus its
 * symbolStates + forwardKeyToIndex + fwdKeyToCurveExpiries entries) per expiry
 * ever subscribed, for the life of the process.
 *
 * The lifetime rule is a refcount, not a boolean: the same forward can be
 * required by several option subjects (two strikes of one expiry) and by several
 * clients at once, so it may only be cancelled when the LAST requirement goes
 * away. This module owns that arithmetic and nothing else — no IB, no timers, no
 * relay state.
 *
 * OWNER granularity is "one client's hold on one client-facing subject". The
 * relay releases by owner (`releaseAllForOwner`) on unsubscribe and on client
 * disconnect, which is exactly the granularity at which holds are created, so a
 * hold can never outlive the subject that created it.
 */

/**
 * @returns {{
 *   acquire: (key: string, owner: string) => {shouldStart: boolean},
 *   release: (key: string, owner: string) => {shouldStop: boolean},
 *   releaseAllForOwner: (owner: string) => string[],
 *   holderCount: (key: string) => number,
 *   keys: () => string[],
 *   owners: () => string[],
 *   isEmpty: () => boolean,
 * }}
 */
export function createForwardSubRegistry() {
  /** key → Set<owner> */
  const keyToOwners = new Map();
  /** owner → Set<key> */
  const ownerToKeys = new Map();

  /**
   * Record `owner`'s requirement for `key`.
   * @returns {{shouldStart: boolean}} true only on the 0 → 1 holder edge, i.e.
   *   the caller must open the underlying IB line.
   */
  function acquire(key, owner) {
    let owners = keyToOwners.get(key);
    const isFirstHolder = owners === undefined || owners.size === 0;
    if (!owners) {
      owners = new Set();
      keyToOwners.set(key, owners);
    }
    owners.add(owner);

    let keys = ownerToKeys.get(owner);
    if (!keys) {
      keys = new Set();
      ownerToKeys.set(owner, keys);
    }
    keys.add(key);

    return { shouldStart: isFirstHolder };
  }

  /**
   * Drop `owner`'s requirement for `key`. Idempotent: releasing a hold that was
   * never acquired is a no-op and leaves no empty entries behind.
   * @returns {{shouldStop: boolean}} true only on the 1 → 0 holder edge, i.e.
   *   the caller must cancel the underlying IB line and drop its state.
   */
  function release(key, owner) {
    const keys = ownerToKeys.get(owner);
    if (keys) {
      keys.delete(key);
      if (keys.size === 0) ownerToKeys.delete(owner);
    }

    const owners = keyToOwners.get(key);
    if (!owners || !owners.delete(owner)) return { shouldStop: false };
    if (owners.size > 0) return { shouldStop: false };
    keyToOwners.delete(key);
    return { shouldStop: true };
  }

  /**
   * Release every hold this owner has (unsubscribe / disconnect).
   * @returns {string[]} keys whose LAST holder just went away, in acquire order.
   */
  function releaseAllForOwner(owner) {
    const keys = ownerToKeys.get(owner);
    if (!keys) return [];
    const stopped = [];
    for (const key of [...keys]) {
      if (release(key, owner).shouldStop) stopped.push(key);
    }
    return stopped;
  }

  return {
    acquire,
    release,
    releaseAllForOwner,
    holderCount: (key) => keyToOwners.get(key)?.size ?? 0,
    keys: () => [...keyToOwners.keys()],
    owners: () => [...ownerToKeys.keys()],
    isEmpty: () => keyToOwners.size === 0 && ownerToKeys.size === 0,
  };
}
