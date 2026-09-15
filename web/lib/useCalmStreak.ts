"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { CalmStreakData } from "./calmStreak";

// GET-only: the calm-streak timer writes the snapshot, so there is no
// manual-scan POST and an hourly poll is plenty. Types live in lib/calmStreak.ts.
const CALM_STREAK_SYNC_CONFIG = {
  endpoint: "/api/calm-streak",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: CalmStreakData) => d.scan_time || null,
};

export function useCalmStreak(): UseSyncReturn<CalmStreakData> {
  return useSyncHook<CalmStreakData>(CALM_STREAK_SYNC_CONFIG, true);
}
