"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Persistent per-table column visibility state.
 *
 * Pass a stable `tableId` (e.g. "positions" / "orders") and the canonical
 * `defaults` map of column key → boolean. The hook returns the merged state
 * (saved overrides take precedence over defaults) plus a toggle for any
 * key. Persisted to localStorage so preferences survive reloads.
 *
 * `alwaysOn` keys cannot be toggled off — useful for identity columns
 * (Ticker, Symbol) and the always-relevant P&L / Status columns.
 */
const STORAGE_PREFIX = "radon:columns:";

export type ColumnVisibility<K extends string> = {
  visible: Record<K, boolean>;
  toggle: (key: K) => void;
  reset: () => void;
};

export function useColumnVisibility<K extends string>(
  tableId: string,
  defaults: Record<K, boolean>,
  alwaysOn: readonly K[] = [],
): ColumnVisibility<K> {
  const storageKey = `${STORAGE_PREFIX}${tableId}`;
  const alwaysOnSet = new Set<K>(alwaysOn);

  const [visible, setVisible] = useState<Record<K, boolean>>(() => ({ ...defaults }));
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    let next = { ...defaults };
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) {
        const saved = JSON.parse(raw) as Partial<Record<K, boolean>>;
        for (const key of Object.keys(saved) as K[]) {
          if (key in next && typeof saved[key] === "boolean") next[key] = saved[key]!;
        }
      }
    } catch {
      next = { ...defaults };
    }
    for (const key of alwaysOnSet) next[key] = true;
    setVisible(next);
    setHydrated(true);
    // Storage hydration is intentionally post-mount to preserve SSR parity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  // Persist on change.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!hydrated) return;
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(visible));
    } catch {
      // ignore quota / private-mode errors
    }
  }, [hydrated, storageKey, visible]);

  const toggle = useCallback(
    (key: K) => {
      if (alwaysOnSet.has(key)) return;
      setVisible((prev) => ({ ...prev, [key]: !prev[key] }));
    },
    // alwaysOnSet is derived from the same `alwaysOn` array — referentially stable per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const reset = useCallback(() => {
    const reset: Record<K, boolean> = { ...defaults };
    for (const key of alwaysOnSet) reset[key] = true;
    setVisible(reset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { visible, toggle, reset };
}
