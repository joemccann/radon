"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import { MarketState } from "./useMarketHours";

/* ─── GEX types (match gex_scan.py JSON output) ──────────────── */

export type GexLevel = {
  strike: number;
  gamma: number;
  distance: number;
  distance_pct: number;
} | null;

export type GexBucket = {
  strike: number;
  call_gex: number;
  put_gex: number;
  net_gex: number;
  pct_from_spot: number;
  tag: string | null;
};

export type GexBias = {
  direction: "BULL" | "CAUTIOUS_BULL" | "NEUTRAL" | "CAUTIOUS_BEAR" | "BEAR";
  reasons: string[];
  days_above_flip: number;
  flip_migration: { date: string; flip: number }[];
};

export type GexHistoryEntry = {
  date: string;
  net_gex: number;
  net_dex: number;
  gex_flip: number | null;
  spot: number;
  atm_iv: number | null;
  vol_pc: number | null;
  bias: string | null;
};

/** MenthorQ-sourced key levels (from key_levels dashboard card). */
export type MqLevels = {
  source_date: string | null;
  spot: number | null;
  hvl: number | null;
  call_resistance_all: number | null;
  call_resistance_0dte: number | null;
  put_support_all: number | null;
  put_support_0dte: number | null;
  expected_high: number | null;
  expected_low: number | null;
  distance_to_hvl_pct: string | null;
  iv30d: number | null;
  hv30: number | null;
  iv_rank: string | null;
  top_gex_strikes: number[];
};

/** Per-level delta between UW and MenthorQ (positive = UW higher). */
export type SourceDeltaEntry = { uw: number; mq: number; delta: number };
export type SourceDelta = {
  flip_vs_hvl?: SourceDeltaEntry;
  put_wall_vs_support_all?: SourceDeltaEntry;
  put_wall_vs_support_0dte?: SourceDeltaEntry;
  call_wall_vs_resistance_all?: SourceDeltaEntry;
  call_wall_vs_resistance_0dte?: SourceDeltaEntry;
};

/** Consolidated IV data with explicit source attribution. */
export type IvData = {
  /** 30D IV from UW iv_rank endpoint (percentage, e.g. 20.4) */
  iv30d: number | null;
  /** 1-year IV rank from UW (0–100) */
  iv_rank: number | null;
  /** HV30 from MenthorQ (percentage) */
  hv30: number | null;
  /** 30D IV from MenthorQ (percentage) */
  mq_iv30d: number | null;
  /** IV rank label from MenthorQ (e.g. "32%") */
  mq_iv_rank: string | null;
  /** Which source(s) provided IV data */
  source: "uw" | "mq" | "both" | null;
};

export type GexData = {
  scan_time: string;
  market_open: boolean;
  ticker: string;
  spot: number;
  close: number | null;
  day_change: number | null;
  day_change_pct: number | null;
  data_date: string;
  net_gex: number;
  net_dex: number;
  atm_iv: number | null;
  vol_pc: number | null;
  levels: {
    gex_flip: GexLevel;
    max_magnet: GexLevel;
    second_magnet: GexLevel;
    max_accelerator: GexLevel;
    put_wall: GexLevel;
    call_wall: GexLevel;
  };
  profile: GexBucket[];
  expected_range: {
    low: number | null;
    high: number | null;
    iv_1d: number | null;
  };
  bias: GexBias;
  history: GexHistoryEntry[];
  /** Structured IV from both sources (replaces the raw atm_iv field for display) */
  iv: IvData | null;
  /** MenthorQ key levels (null when MQ unavailable or --no-mq used) */
  mq: MqLevels | null;
  /** UW vs MQ level deltas (null when mq is null) */
  source_delta: SourceDelta | null;
};

/* ─── Staleness check ────────────────────────────────────────── */

function todayET(): string {
  return new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
}

function needsGexRetry(data: GexData | null | undefined): boolean {
  if (!data?.scan_time) return true;
  try {
    const scanDate = new Date(data.scan_time).toLocaleDateString("sv", { timeZone: "America/New_York" });
    return scanDate !== todayET();
  } catch {
    return true;
  }
}

/* ─── Hook ───────────────────────────────────────────────────── */

const GEX_SYNC_CONFIG = {
  endpoint: "/api/gex",
  interval: 60_000,
  hasPost: false,
  extractTimestamp: (d: GexData) => d.scan_time || null,
  shouldRetry: (d: GexData) => needsGexRetry(d),
  retryIntervalMs: 5000,
  retryMethod: "GET" as const,
};

export function useGex(marketState: MarketState | null = null): UseSyncReturn<GexData> {
  let active: boolean;
  if (marketState === MarketState.OPEN || marketState === MarketState.EXTENDED) {
    active = true;
  } else if (marketState === MarketState.CLOSED) {
    active = false;
  } else {
    active = true;
  }

  const config = {
    ...GEX_SYNC_CONFIG,
    interval: marketState === MarketState.EXTENDED ? 300_000 : 60_000,
  };

  return useSyncHook(config, active);
}
