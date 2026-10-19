"use client";

import { useMemo, useState, useCallback } from "react";
import { useSyncHook } from "./useSyncHook";
import type { TradeLogData, TradeEntry } from "./types";

/**
 * Pull a per-trade timestamp from whichever field carries it. Trades
 * may have `filled_at` (post 2026-05 rehydrate), `date` (legacy), or
 * `close_date` (closed positions). Return null when none are present.
 */
function timestampFor(trade: TradeEntry): string | null {
  const candidate =
    (typeof (trade as unknown as { filled_at?: unknown }).filled_at === "string"
      ? (trade as unknown as { filled_at: string }).filled_at
      : null) ??
    (typeof (trade as unknown as { date?: unknown }).date === "string"
      ? (trade as unknown as { date: string }).date
      : null) ??
    (typeof trade.close_date === "string" ? trade.close_date : null);
  return candidate ?? null;
}

/**
 * Derive lastSync from the data, not the response arrival time.
 *
 * Same bug class that hid the libsql freeze for 7 hours: when
 * `extractTimestamp` returned `new Date().toISOString()`, the staleness
 * banner stamped every successful fetch as "fresh now" even when the
 * underlying journal was hours old. Using the latest per-trade
 * timestamp surfaces real staleness; an empty trades array returns
 * null so the staleness UI shows "no data" instead of "fresh".
 */
function latestTradeTimestamp(d: TradeLogData): string | null {
  if (!d?.trades?.length) return null;
  let max: string | null = null;
  for (const trade of d.trades) {
    const candidate = timestampFor(trade);
    if (candidate && (!max || candidate > max)) max = candidate;
  }
  return max;
}

const config = {
  endpoint: "/api/journal",
  interval: 0,
  hasPost: true,
  extractTimestamp: latestTradeTimestamp,
};

/** Test-only export so the data-derived contract is verifiable from a unit test. */
export const __TEST_CONFIG__ = config;

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
    // The demo has no IB account and /api/journal/sync is operatorOnly: re-read
    // the sample journal instead of importing.
    if (process.env.NEXT_PUBLIC_RADON_DEMO === "1") {
      result.syncNow();
      return { imported: 0, skipped: 0 };
    }
    setSyncing(true);
    try {
      const res = await fetch("/api/journal/sync", { method: "POST", cache: "no-store" });
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
