"use client";

import { useCallback, useEffect, useState } from "react";

export type AlertRule = {
  id: string;
  ticker: string;
  metric: string;
  op: string;
  threshold: number;
  channel: string;
  created_at: string;
  last_fired_at: string | null;
};

export type AlertRuleDraft = {
  ticker: string;
  metric: string;
  op: string;
  threshold: number;
  channel?: string;
};

type UseAlertsReturn = {
  rules: AlertRule[];
  isLoading: boolean;
  error: string | null;
  createRule: (draft: AlertRuleDraft) => Promise<void>;
  deleteRule: (id: string) => Promise<void>;
};

export function useAlerts(): UseAlertsReturn {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/alerts", { cache: "no-store" });
      if (!res.ok) throw new Error("Failed to load alert rules");
      const json = (await res.json()) as { rules: AlertRule[] };
      setRules(Array.isArray(json.rules) ? json.rules : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load alert rules");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const createRule = useCallback(
    async (draft: AlertRuleDraft) => {
      const res = await fetch("/api/alerts", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (!res.ok) throw new Error("Failed to create alert rule");
      await load();
    },
    [load],
  );

  const deleteRule = useCallback(
    async (id: string) => {
      const res = await fetch(`/api/alerts/${encodeURIComponent(id)}`, {
        method: "DELETE",
        cache: "no-store",
      });
      if (!res.ok) throw new Error("Failed to delete alert rule");
      await load();
    },
    [load],
  );

  return { rules, isLoading, error, createRule, deleteRule };
}
