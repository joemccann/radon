"use client";

import { useSyncHook, type UseSyncReturn } from "@/lib/useSyncHook";
import type { AtsVenueShareData } from "./atsVenueShare";

// GET-only: FINRA publishes the off-exchange file weekly and the refresh timer
// runs the fetcher directly, so there is no manual-scan POST and an hourly poll
// is already far ahead of the source.
const ATS_VENUE_SHARE_SYNC_CONFIG = {
  endpoint: "/api/equibles-ats-venue-share",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (data: AtsVenueShareData) => data.scan_time,
};

export function useAtsVenueShare(): UseSyncReturn<AtsVenueShareData> {
  return useSyncHook<AtsVenueShareData>(ATS_VENUE_SHARE_SYNC_CONFIG, true);
}
