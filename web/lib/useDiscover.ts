"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { DiscoverData } from "./types";

const config = {
  endpoint: "/api/discover",
  extractTimestamp: (d: DiscoverData) => d.discovery_time || null,
};

export function useDiscover(active: boolean): UseSyncReturn<DiscoverData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<DiscoverData>(stableConfig, active);
}
