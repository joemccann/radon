"use client";

import { useMemo, useState, useCallback } from "react";
import { useSyncHook } from "./useSyncHook";
import type { TradeLogData } from "./types";

const config = {
  endpoint: "/api/journal",
  interval: 0,
  hasPost: true,
  extractTimestamp: (_d: TradeLogData) => new Date().toISOString(),
};

export type UseJournalReturn = {
  data: TradeLogData | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  syncWithIB: () => Promise<{ imported: number; skipped: number }>;
  syncing: boolean;
  lastSyncResult: { imported: number; skipped: number } | null;
};

export function useJournal(active = true): UseJournalReturn {
  const stableConfig = useMemo(() => config, []);
  const result = useSyncHook<TradeLogData>(stableConfig, active);
  const [syncing, setSyncing] = useState(false);
  const [lastSyncResult, setLastSyncResult] = useState<{ imported: number; skipped: number } | null>(null);

  const syncWithIB = useCallback(async () => {
    setSyncing(true);
    try {
      const res = await fetch("/api/journal/sync", { method: "POST" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || "Sync failed");
      setLastSyncResult({ imported: body.imported, skipped: body.skipped });
      // Refresh journal data after successful sync
      result.syncNow();
      return { imported: body.imported, skipped: body.skipped };
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Sync failed";
      throw new Error(msg);
    } finally {
      setSyncing(false);
    }
  }, [result]);

  return {
    data: result.data,
    loading: result.loading,
    error: result.error,
    refresh: result.syncNow,
    syncWithIB,
    syncing,
    lastSyncResult,
  };
}
