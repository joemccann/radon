"use client";

import { useCallback, useEffect, useState } from "react";
import { hydrateUiPreferences, saveUiColumns } from "@/lib/uiPreferences";

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
    let hadLocal = false;
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) {
        hadLocal = true;
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

    // Cross-device restore: the profile row fills in only when this device
    // has no localStorage entry yet — a device-local choice stays untouched.
    if (!hadLocal) {
      let active = true;
      void hydrateUiPreferences().then((prefs) => {
        if (!active) return;
        const server = prefs.columns?.[tableId];
        if (!server) return;
        const merged = { ...defaults };
        for (const key of Object.keys(server) as K[]) {
          if (key in merged && typeof server[key] === "boolean") merged[key] = server[key];
        }
        for (const key of alwaysOnSet) merged[key] = true;
        setVisible(merged);
      });
      return () => {
        active = false;
      };
    }
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
      setVisible((prev) => {
        const next = { ...prev, [key]: !prev[key] };
        // Server sync happens ONLY on a user action, never on hydration —
        // pushing hydrated defaults would clobber another device's choice.
        saveUiColumns(tableId, next);
        return next;
      });
    },
    // alwaysOnSet is derived from the same `alwaysOn` array — referentially stable per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const reset = useCallback(() => {
    const reset: Record<K, boolean> = { ...defaults };
    for (const key of alwaysOnSet) reset[key] = true;
    setVisible(reset);
    saveUiColumns(tableId, reset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { visible, toggle, reset };
}
