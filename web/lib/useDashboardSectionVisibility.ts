"use client";

import { useCallback, useEffect, useState } from "react";

export const DASHBOARD_SECTION_IDS = [
  "portfolio",
  "news",
  "orders",
  "opportunities",
  "flow-surprise",
  "catalysts",
] as const;

const STORAGE_KEY = "radon:mobile-dashboard:hidden-sections";
const SECTION_ID_SET = new Set<string>(DASHBOARD_SECTION_IDS);

export type DashboardSectionVisibility = {
  isHidden: (id: string) => boolean;
  toggle: (id: string) => void;
};

function normalizeHiddenIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const ids = [
    ...new Set(value.filter((item): item is string => SECTION_ID_SET.has(item))),
  ];

  // Fail open. A legacy/corrupt state with every section hidden makes the
  // dashboard look blank and survives reloads.
  return ids.length >= DASHBOARD_SECTION_IDS.length ? [] : ids;
}

function readHiddenIds(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? normalizeHiddenIds(JSON.parse(raw)) : [];
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
      // Ignore quota / private-mode errors.
    }
  }, [hasMounted, hidden]);

  const isHidden = useCallback((id: string) => hidden.has(id), [hidden]);

  const toggle = useCallback((id: string) => {
    if (!SECTION_ID_SET.has(id)) return;

    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        return next;
      }

      // Keep at least one section visible. This preserves the collapse
      // affordance without allowing a persistent all-blank dashboard.
      if (next.size >= DASHBOARD_SECTION_IDS.length - 1) return next;
      next.add(id);
      return next;
    });
  }, []);

  return { isHidden, toggle };
}
