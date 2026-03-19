"use client";

import type { BlotterData } from "./types";
import { useSyncHook } from "./useSyncHook";

type UseBlotterReturn = {
  data: BlotterData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  syncNow: () => void;
};

export function useBlotter(active = false): UseBlotterReturn {
  const result = useSyncHook<BlotterData>(
    {
      endpoint: "/api/blotter",
      extractTimestamp: (data) => data.as_of || null,
    },
    active,
  );

  return {
    data: result.data,
    loading: result.loading,
    syncing: result.syncing,
    error: result.error,
    syncNow: result.syncNow,
  };
}
