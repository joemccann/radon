"use client";

import { useSyncHook, type UseSyncReturn } from "@/lib/useSyncHook";
import type { ShortCrowdingData } from "./shortCrowding";

// GET-only: the refresh timer owns the Equibles pull (short interest settles
// bi-monthly, the squeeze score moves daily), so there is no manual-scan POST
// and an hourly poll is already generous.
const SHORT_CROWDING_SYNC_CONFIG = {
  endpoint: "/api/equibles-short-crowding",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: ShortCrowdingData) => d.scan_time || null,
};

export function useEquiblesShortCrowding(): UseSyncReturn<ShortCrowdingData> {
  return useSyncHook<ShortCrowdingData>(SHORT_CROWDING_SYNC_CONFIG, true);
}
