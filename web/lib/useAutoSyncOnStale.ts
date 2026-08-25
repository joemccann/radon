"use client";

import { useEffect } from "react";

import {
  claimAutoSyncFire,
  type AutoSyncState,
  type ClaimStore,
} from "./autoSyncClaim";

type TargetState = AutoSyncState;

/**
 * MODULE scope, deliberately (R-105). The map used to be a `useRef` per
 * `WorkspaceShell` instance, so every route remount started empty and every
 * open tab in a browser ran its own cooldown against a per-operator rate
 * limiter. Two hooks pointed at the same target now share one window.
 *
 * Same-origin tabs additionally share it through `localStorage`; a private
 * window or a storage exception just falls back to the in-memory map. The
 * read-then-write itself is serialized across tabs by `claimAutoSyncFire`.
 */
const stateByTarget = new Map<string, TargetState>();

const STORAGE_PREFIX = "radon:autosync:";

function readState(target: string): TargetState | null {
  const memory = stateByTarget.get(target);
  let stored: TargetState | null = null;
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + target);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<TargetState>;
      if (typeof parsed.lastFiredAt === "number") {
        stored = {
          lastFiredAt: parsed.lastFiredAt,
          consecutive: Number(parsed.consecutive) || 0,
        };
      }
    }
  } catch {
    stored = null;
  }
  if (!memory) return stored;
  if (!stored) return memory;
  return stored.lastFiredAt > memory.lastFiredAt ? stored : memory;
}

function writeState(target: string, state: TargetState): void {
  stateByTarget.set(target, state);
  try {
    window.localStorage.setItem(STORAGE_PREFIX + target, JSON.stringify(state));
  } catch {
    /* private window / quota — the in-memory map still bounds this tab */
  }
}

const store: ClaimStore = { read: readState, write: writeState };

function webLocks(): LockManager | undefined {
  return typeof navigator !== "undefined" ? navigator.locks : undefined;
}

/** Test seam: drop every target's cooldown. */
export function resetAutoSyncCooldowns(): void {
  stateByTarget.clear();
  try {
    for (let i = window.localStorage.length - 1; i >= 0; i -= 1) {
      const key = window.localStorage.key(i);
      if (key?.startsWith(STORAGE_PREFIX)) window.localStorage.removeItem(key);
    }
  } catch {
    /* ignore */
  }
}

/**
 * Fires the page's producer sync when a render surfaces a stale snapshot,
 * so freshness never waits on the operator pressing SYNC. The backend
 * orders/portfolio loops run on a 5-minute cadence; landing on a page
 * between ticks used to serve a snapshot up to that old with nothing
 * refreshing it (the client-side auto-POST was removed in 43a02eff).
 *
 * R-105: a FAILED producer sync returns HTTP 200 — the route re-serves the
 * Turso snapshot with `X-Sync-Warning` and an UNCHANGED `last_sync` — so
 * `stale` never cleared and this effect re-fired on every 60s cooldown
 * forever: ~1440 gateway connect attempts per day per tab across a weekend
 * or a 2FA window. Each consecutive fire that does not clear staleness now
 * doubles the window (1, 2, 4, 8, 16 minutes), and a snapshot that actually
 * refreshes resets the ladder.
 */
export function useAutoSyncOnStale(
  stale: boolean,
  syncNow: () => void,
  target: string,
  enabled: boolean,
  tick = 0,
): void {
  useEffect(() => {
    if (!enabled) return;
    if (!stale) {
      // The producer succeeded: the next stale window starts fresh. Reset
      // from the merged view so a tab that never won a claim still clears
      // the shared ladder, and a stale in-memory timestamp never rewinds it.
      const current = readState(target);
      if (current && current.consecutive !== 0) {
        writeState(target, { lastFiredAt: current.lastFiredAt, consecutive: 0 });
      }
      return;
    }
    const claimed = claimAutoSyncFire({ target, now: Date.now(), store, locks: webLocks() });
    if (claimed === true) {
      syncNow();
      return;
    }
    if (claimed === false) return;
    // A claim already wrote the cooldown, so the fire must happen even if the
    // effect re-ran meanwhile (StrictMode double-invoke, a 30 s tick); the
    // sync hooks guard their own state updates against unmount.
    void claimed.then((ok) => {
      if (ok) syncNow();
    });
  }, [stale, enabled, target, syncNow, tick]);
}
