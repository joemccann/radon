"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { StrengthConfirmationData } from "./types";

const config = {
  endpoint: "/api/scanner/strength",
  hasPost: false,
  extractTimestamp: (d: StrengthConfirmationData) => d.scan_time || null,
  loadWhenInactive: false,
};

export function useStrengthConfirmation(active: boolean): UseSyncReturn<StrengthConfirmationData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<StrengthConfirmationData>(stableConfig, active);
}
