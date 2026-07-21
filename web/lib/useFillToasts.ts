"use client";

import { useEffect, useRef } from "react";
import type { OrdersData } from "./types";
import type { ToastType } from "./useToast";
import { shouldMarkDemoSignup } from "./demo/demoSignup";
import {
  diffNewFills,
  execKey,
  formatFillToast,
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
  addToast: (type: ToastType, message: string, duration?: number) => string,
): void {
  const primedRef = useRef(false);
  const seenRef = useRef<Set<string>>(new Set());

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
      addToast("success", formatFillToast(fill), 0);
      const key = execKey(fill);
      if (key) seenRef.current.add(key);
    }
    if (storage) saveSeen(storage, seenRef.current);
  }, [orders, addToast]);
}
