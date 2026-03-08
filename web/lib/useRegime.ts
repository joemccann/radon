"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";

export type CriHistoryEntry = {
  date: string;
  vix: number;
  vvix: number;
  spy: number;
  spx_vs_ma_pct: number;
  vix_5d_roc: number;
};

export type CriData = {
  scan_time: string;
  market_open?: boolean;
  date: string;
  vix: number;
  vvix: number;
  spy: number;
  vix_5d_roc: number;
  vvix_vix_ratio: number | null;
  spx_100d_ma: number | null;
  spx_distance_pct: number;
  avg_sector_correlation: number | null;
  corr_5d_change: number | null;
  realized_vol: number | null;
  cri: {
    score: number;
    level: string;
    components: {
      vix: number;
      vvix: number;
      correlation: number;
      momentum: number;
    };
  };
  cta: {
    realized_vol: number;
    exposure_pct: number;
    forced_reduction_pct: number;
    est_selling_bn: number;
  };
  menthorq_cta: {
    date: string;
    source: string;
    spx: Record<string, unknown> | null;
    tables: Record<string, unknown[]>;
  } | null;
  crash_trigger: {
    triggered: boolean;
    conditions: {
      spx_below_100d_ma: boolean;
      realized_vol_gt_25: boolean;
      avg_correlation_gt_060: boolean;
    };
    values: Record<string, unknown>;
  };
  history: CriHistoryEntry[];
};

const config = {
  endpoint: "/api/regime",
  extractTimestamp: (d: CriData) => d.scan_time || null,
};

export function useRegime(active: boolean): UseSyncReturn<CriData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<CriData>(stableConfig, active);
}
