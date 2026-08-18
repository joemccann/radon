"use client";

import { useEffect, useRef } from "react";

const AUTO_SYNC_COOLDOWN_MS = 60_000;

/**
 * Fires the page's producer sync when a render surfaces a stale snapshot,
 * so freshness never waits on the operator pressing SYNC. The backend
 * orders/portfolio loops run on a 5-minute cadence; landing on a page
 * between ticks used to serve a snapshot up to that old with nothing
 * refreshing it (the client-side auto-POST was removed in 43a02eff).
 *
 * Cooldown is per sync target so rapid route flips cannot hammer the
 * rate-limited refresh routes, and a failing producer is retried at most
 * once per window.
 */
export function useAutoSyncOnStale(
  stale: boolean,
  syncNow: () => void,
  target: string,
  enabled: boolean,
): void {
  const lastFiredAtByTargetRef = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    if (!stale || !enabled) return;
    const now = Date.now();
    const lastFiredAt = lastFiredAtByTargetRef.current.get(target) ?? 0;
    if (now - lastFiredAt < AUTO_SYNC_COOLDOWN_MS) return;
    lastFiredAtByTargetRef.current.set(target, now);
    syncNow();
  }, [stale, enabled, target, syncNow]);
}
