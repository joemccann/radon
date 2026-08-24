"use client";

import { useEffect } from "react";

const AUTO_SYNC_COOLDOWN_MS = 60_000;
/** Cap the backoff so a producer that recovers is retried within ~16 min. */
const MAX_BACKOFF_MULTIPLIER = 16;

type TargetState = { lastFiredAt: number; consecutive: number };

/**
 * MODULE scope, deliberately (R-105). The map used to be a `useRef` per
 * `WorkspaceShell` instance, so every route remount started empty and every
 * open tab in a browser ran its own cooldown against a per-operator rate
 * limiter. Two hooks pointed at the same target now share one window.
 *
 * Same-origin tabs additionally share it through `localStorage`; a private
 * window or a storage exception just falls back to the in-memory map.
 */
const stateByTarget = new Map<string, TargetState>();

const STORAGE_PREFIX = "radon:autosync:";

function readState(target: string): TargetState {
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
  if (!memory) return stored ?? { lastFiredAt: 0, consecutive: 0 };
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
      // The producer succeeded: the next stale window starts fresh.
      const current = stateByTarget.get(target);
      if (current && current.consecutive !== 0) {
        writeState(target, { lastFiredAt: current.lastFiredAt, consecutive: 0 });
      }
      return;
    }
    const now = Date.now();
    const state = readState(target);
    const multiplier = Math.min(
      MAX_BACKOFF_MULTIPLIER,
      2 ** Math.max(0, state.consecutive),
    );
    if (now - state.lastFiredAt < AUTO_SYNC_COOLDOWN_MS * multiplier) return;
    writeState(target, { lastFiredAt: now, consecutive: state.consecutive + 1 });
    syncNow();
  }, [stale, enabled, target, syncNow, tick]);
}
