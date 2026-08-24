"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export const SNAPSHOT_STALE_THRESHOLD_MS = 60_000;
const STALENESS_TICK_MS = 30_000;

/**
 * `unknown` is a BLACKOUT, not health (R-149). `lastSync` is null exactly
 * when the producer has never written a snapshot this session, when the GET
 * returned an error body, or when `extractTimestamp` yielded null — and all
 * three used to report `isStale: false`, hiding the stale pill, gating
 * `useAutoSyncOnStale` off entirely and degrading the label to the reassuring
 * "Awaiting first sample".
 */
export type SnapshotState = "unknown" | "fresh" | "stale";

export type SnapshotStaleness = {
  /** True for `stale` AND `unknown`: neither is a snapshot you can trust. */
  isStale: boolean;
  state: SnapshotState;
  staleAgeMinutes: number | null;
  /** Advances on every re-evaluation. Pass into stale-driven effects
   *  (useAutoSyncOnStale) so retries share this cadence instead of dying
   *  after the first attempt when the stale boolean never transitions. */
  tick: number;
};

function evaluate(lastSync: string | null, now: number): {
  state: SnapshotState;
  staleAgeMinutes: number | null;
} {
  if (!lastSync) return { state: "unknown", staleAgeMinutes: null };
  const syncedAt = new Date(lastSync).getTime();
  if (Number.isNaN(syncedAt)) return { state: "unknown", staleAgeMinutes: null };
  const ageMs = now - syncedAt;
  return {
    state: ageMs > SNAPSHOT_STALE_THRESHOLD_MS ? "stale" : "fresh",
    staleAgeMinutes: Math.max(1, Math.floor(ageMs / 60_000)),
  };
}

/**
 * Session-independent snapshot staleness. After-hours, overnight and
 * weekend fills exist, so freshness is never gated on market state —
 * a snapshot older than the threshold is stale whenever it renders.
 *
 * Re-evaluates on an internal clock: without the tick, staleness was
 * only recomputed when lastSync changed, so a page left open went
 * silently stale once the backend producer's cadence lapsed.
 *
 * R-139: the tick only advances when it can CHANGE something — a fresh
 * snapshot crossing into stale, or the retry cadence a `stale` / `unknown`
 * snapshot rides on. `tick` is in the returned memo, so bumping it in the
 * healthy steady state re-rendered WorkspaceShell and every non-memoised
 * child every 30s in every open tab for nothing.
 */
export function useSnapshotStaleness(lastSync: string | null): SnapshotStaleness {
  const [tick, setTick] = useState(0);
  const stateRef = useRef<SnapshotState>(evaluate(lastSync, Date.now()).state);

  useEffect(() => {
    stateRef.current = evaluate(lastSync, Date.now()).state;
    const id = setInterval(() => {
      const next = evaluate(lastSync, Date.now()).state;
      const changed = next !== stateRef.current;
      stateRef.current = next;
      if (changed || next !== "fresh") setTick((t) => t + 1);
    }, STALENESS_TICK_MS);
    return () => clearInterval(id);
  }, [lastSync]);

  return useMemo(() => {
    const { state, staleAgeMinutes } = evaluate(lastSync, Date.now());
    return { isStale: state !== "fresh", state, staleAgeMinutes, tick };
  }, [lastSync, tick]);
}
