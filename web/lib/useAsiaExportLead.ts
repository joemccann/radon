"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { AsiaExportLeadData } from "./asiaExportLead";

// The daily writer owns refreshes. The client only reads its saved snapshot.
const ASIA_EXPORT_LEAD_SYNC_CONFIG = {
  endpoint: "/api/asia-export-lead",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (data: AsiaExportLeadData) => data.scan_time || null,
};

export function useAsiaExportLead(): UseSyncReturn<AsiaExportLeadData> {
  return useSyncHook<AsiaExportLeadData>(ASIA_EXPORT_LEAD_SYNC_CONFIG, true);
}
