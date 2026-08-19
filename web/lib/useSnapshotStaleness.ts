"use client";

import { useEffect, useMemo, useState } from "react";

export const SNAPSHOT_STALE_THRESHOLD_MS = 60_000;
const STALENESS_TICK_MS = 30_000;

export type SnapshotStaleness = {
  isStale: boolean;
  staleAgeMinutes: number | null;
  /** Advances on every re-evaluation. Pass into stale-driven effects
   *  (useAutoSyncOnStale) so retries share this cadence instead of dying
   *  after the first attempt when the stale boolean never transitions. */
  tick: number;
};

/**
 * Session-independent snapshot staleness. After-hours, overnight and
 * weekend fills exist, so freshness is never gated on market state —
 * a snapshot older than the threshold is stale whenever it renders.
 *
 * Re-evaluates on an internal clock: without the tick, staleness was
 * only recomputed when lastSync changed, so a page left open went
 * silently stale once the backend producer's cadence lapsed.
 */
export function useSnapshotStaleness(lastSync: string | null): SnapshotStaleness {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), STALENESS_TICK_MS);
    return () => clearInterval(id);
  }, []);

  return useMemo(() => {
    if (!lastSync) return { isStale: false, staleAgeMinutes: null, tick };
    const syncedAt = new Date(lastSync).getTime();
    if (Number.isNaN(syncedAt)) return { isStale: false, staleAgeMinutes: null, tick };
    const ageMs = Date.now() - syncedAt;
    return {
      isStale: ageMs > SNAPSHOT_STALE_THRESHOLD_MS,
      staleAgeMinutes: Math.max(1, Math.floor(ageMs / 60_000)),
      tick,
    };
  }, [lastSync, tick]);
}
