"use client";

import { useEffect, useRef } from "react";
import type { OrdersData } from "./types";
import type { ToastType } from "./useToast";
import { shouldMarkDemoSignup } from "./demo/demoSignup";
import type { ExecutedOrder } from "./types";
import {
  diffNewFills,
  execKey,
  fillGroupKey,
  formatFillToast,
  mergeFill,
  hasSeenBaseline,
  loadSeen,
  primeSeen,
  saveSeen,
  type SeenStorage,
} from "./fillToasts";

/**
 * Property ACCESS on window.sessionStorage throws SecurityError in Chromium
 * when site data is blocked (and in some embedded webviews) — the try/catch
 * must wrap the access itself, not just usage.
 */
function safeSessionStorage(): SeenStorage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * Watches the globally-polled OrdersData for new execution rows and raises a
 * persistent (manual-dismiss) success toast per fill.
 *
 * Baseline semantics: the very first payload of the browser session primes
 * the seen set WITHOUT toasting (pre-existing fills never toast on load).
 * The primed baseline persists to sessionStorage; a route-nav remount
 * restores it and diffs the first payload against it, so a fill that lands
 * mid-navigation still toasts instead of being silently absorbed by a
 * re-prime.
 */
export function useFillToasts(
  orders: OrdersData | null,
  upsertToast: (key: string, type: ToastType, message: string, duration?: number) => string,
  onNewFills?: () => void,
  isToastLive?: (key: string) => boolean,
): void {
  const primedRef = useRef(false);
  const seenRef = useRef<Set<string>>(new Set());
  // Running total per order, so a partial-fill sequence rewrites one toast
  // instead of stacking one per execution.
  const groupsRef = useRef<Map<string, ExecutedOrder>>(new Map());
  // Read through a ref so a caller passing an inline closure cannot re-run the
  // diff effect (and re-fire the producer sync) on every render.
  const onNewFillsRef = useRef(onNewFills);
  onNewFillsRef.current = onNewFills;

  useEffect(() => {
    if (shouldMarkDemoSignup()) return;
    if (orders == null) return;

    const storage = safeSessionStorage();
    const executed = orders.executed_orders ?? [];

    if (!primedRef.current) {
      primedRef.current = true;
      const restored = storage != null && hasSeenBaseline(storage);
      const seen = storage ? loadSeen(storage) : new Set<string>();
      seenRef.current = seen;
      if (!restored) {
        for (const key of primeSeen(executed)) seen.add(key);
        if (storage) saveSeen(storage, seen);
        return;
      }
      // Restored baseline: fall through and diff this first payload, so
      // fills that landed while unmounted are announced.
    }

    const fresh = diffNewFills(seenRef.current, executed);
    if (fresh.length === 0) return;
    for (const fill of fresh) {
      const group = fillGroupKey(fill);
      // A dismissed toast's running total is forgotten: the next fill for the
      // group opens a fresh toast reading its own quantity, not the cumulative
      // total presented as a new fill (R-642).
      if (isToastLive && !isToastLive(group)) groupsRef.current.delete(group);
      const running = mergeFill(groupsRef.current.get(group) ?? null, fill);
      groupsRef.current.set(group, running);
      upsertToast(group, "success", formatFillToast(running), 0);
      const key = execKey(fill);
      if (key) seenRef.current.add(key);
    }
    if (storage) saveSeen(storage, seenRef.current);
    // A fill is the earliest evidence the app has that positions changed.
    // Without this the positions table waited on its own producer timer and
    // rendered pre-fill state underneath a FILLED toast.
    onNewFillsRef.current?.();
  }, [orders, upsertToast, isToastLive]);
}
