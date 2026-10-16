"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PortfolioPosition } from "@/lib/types";
import {
  defaultStrategyGroups, projectStrategyGroups, strategySourceIdentity,
  suggestStrategyGroups, validateStrategyGroups,
  type StrategyGroup, type StrategyGroupPlan,
} from "@/lib/positionStrategyGroups";

export const POSITION_STRATEGY_GROUPS_STORAGE_KEY = "radon:position-strategy-groups:v1";
type Plans = Record<string, StrategyGroupPlan>;

function persistentPlans(plans: Plans): Plans {
  return Object.fromEntries(Object.entries(plans).filter(([, plan]) => plan.persistent));
}

/** Browser-local layout only. Broker state and account aggregates never change. */
export function usePositionStrategyGroups(sources: PortfolioPosition[]) {
  const [plans, setPlans] = useState<Plans>({});
  const plansRef = useRef<Plans>({});
  const previousRef = useRef<Plans | null>(null);
  const [canUndo, setCanUndo] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(POSITION_STRATEGY_GROUPS_STORAGE_KEY);
      if (raw) {
        if (raw.length > 512_000) throw new Error("Oversized grouping preferences");
        const saved: unknown = JSON.parse(raw);
        if (saved && typeof saved === "object" && "version" in saved && saved.version === 1 && "plans" in saved
          && saved.plans && typeof saved.plans === "object" && !Array.isArray(saved.plans)) {
          const next: Plans = {};
          for (const [key, input] of Object.entries(saved.plans).slice(0, 500)) {
            if (!input || typeof input !== "object" || input.sourceKey !== key || typeof input.fingerprint !== "string"
              || input.persistent !== true || !Array.isArray(input.groups)) continue;
            next[key] = input as StrategyGroupPlan;
          }
          plansRef.current = next;
          setPlans(next);
        }
      }
    } catch {
      setError("Saved position groups could not be loaded. Your broker positions are unchanged.");
    }
    setHydrated(true);
  }, []);

  const commit = useCallback((next: Plans, remember = true): boolean => {
    if (!hydrated) {
      setError("Position grouping preferences are still loading. Try again.");
      return false;
    }
    try {
      const before = JSON.stringify(persistentPlans(plansRef.current));
      const after = JSON.stringify(persistentPlans(next));
      if (before !== after) window.localStorage.setItem(POSITION_STRATEGY_GROUPS_STORAGE_KEY, JSON.stringify({ version: 1, plans: persistentPlans(next) }));
    } catch {
      setError("Position groups could not be saved. Your previous layout and broker positions are unchanged.");
      return false;
    }
    if (remember) {
      previousRef.current = plansRef.current;
      setCanUndo(true);
    }
    plansRef.current = next;
    setPlans(next);
    setError(null);
    return true;
  }, [hydrated]);

  const currentPlan = useCallback((source: PortfolioPosition): StrategyGroupPlan | null => {
    const identity = strategySourceIdentity(source);
    if (!identity) return null;
    if (sources.filter(candidate => strategySourceIdentity(candidate)?.key === identity.key).length !== 1) return null;
    const plan = plans[identity.key];
    return plan?.persistent === identity.persistent && plan.fingerprint === identity.fingerprint
      && validateStrategyGroups(source, plan.groups) ? plan : null;
  }, [plans, sources]);

  const projection = useMemo(() => {
    const positions: PortfolioPosition[] = [];
    const sourceById = new Map<number, PortfolioPosition>();
    const sourceByRow = new Map<PortfolioPosition, PortfolioPosition>();
    const usedIds = new Set(sources.map(source => source.id));
    for (const source of sources) {
      const plan = currentPlan(source);
      const projected = plan ? projectStrategyGroups(source, plan.groups) : null;
      for (const row of projected ?? [source]) {
        if (row !== source && row.id !== source.id) {
          while (usedIds.has(row.id)) row.id--;
          usedIds.add(row.id);
        }
        sourceById.set(row.id, source);
        sourceByRow.set(row, source);
        positions.push(row);
      }
    }
    return { positions, sourceById, sourceByRow };
  }, [sources, currentPlan]);

  const apply = useCallback((source: PortfolioPosition, value: StrategyGroup[]): boolean => {
    const identity = strategySourceIdentity(source);
    const matching = identity ? sources.filter(candidate => strategySourceIdentity(candidate)?.key === identity.key) : [];
    const live = matching.length === 1 ? matching[0] : null;
    const groups = live && validateStrategyGroups(live, value);
    if (!identity || !live || strategySourceIdentity(live)?.fingerprint !== identity.fingerprint || !groups || !projectStrategyGroups(live, groups)) {
      setError("The position changed or its groups are incomplete. Review the current legs and try again.");
      return false;
    }
    return commit({ ...plansRef.current, [identity.key]: { sourceKey: identity.key, fingerprint: identity.fingerprint, persistent: identity.persistent, groups } });
  }, [sources, commit]);

  const reset = useCallback((source: PortfolioPosition): boolean => {
    const identity = strategySourceIdentity(source);
    if (!identity) return false;
    const next = { ...plansRef.current };
    delete next[identity.key];
    return commit(next);
  }, [commit]);

  const undo = useCallback((): boolean => {
    const previous = previousRef.current;
    if (!previous || !commit(previous, false)) return false;
    previousRef.current = null;
    setCanUndo(false);
    return true;
  }, [commit]);

  return {
    positions: projection.positions,
    sourceFor: (row: PortfolioPosition) => projection.sourceByRow.get(row) ?? projection.sourceById.get(row.id) ?? row,
    groupsFor: (source: PortfolioPosition) => currentPlan(source)?.groups ?? defaultStrategyGroups(source),
    suggestionFor: suggestStrategyGroups,
    preview: projectStrategyGroups,
    apply, reset, undo, canUndo, error,
    clearError: () => setError(null),
  };
}
