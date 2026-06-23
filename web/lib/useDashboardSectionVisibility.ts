"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Persists which dashboard sections (01-05 etc.) the user has hidden.
 *
 * Sections default to shown. A hidden section id is stored in a Set persisted
 * to localStorage so the collapsed state survives a page reload.
 *
 * SSR safety: the first render (server + client hydration) MUST return the
 * default "nothing hidden" state, never reading localStorage, or React #418
 * hydration mismatch fires. Stored hidden ids are rehydrated in a mount effect
 * and written back on every toggle.
 */
const STORAGE_KEY = "radon:mobile-dashboard:hidden-sections";

export type DashboardSectionVisibility = {
  isHidden: (id: string) => boolean;
  toggle: (id: string) => void;
};

function readHiddenIds(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is string => typeof value === "string");
  } catch {
    return [];
  }
}

export function useDashboardSectionVisibility(): DashboardSectionVisibility {
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [hasMounted, setHasMounted] = useState(false);

  useEffect(() => {
    setHidden(new Set(readHiddenIds()));
    setHasMounted(true);
  }, []);

  useEffect(() => {
    if (!hasMounted || typeof window === "undefined") return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...hidden]));
    } catch {
      // ignore quota / private-mode errors
    }
  }, [hasMounted, hidden]);

  const isHidden = useCallback((id: string) => hidden.has(id), [hidden]);

  const toggle = useCallback((id: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  return { isHidden, toggle };
}
