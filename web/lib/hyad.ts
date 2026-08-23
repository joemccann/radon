/**
 * HYAD — FINRA TRACE high yield bond cumulative advance-decline line.
 * Payload types + pure helpers for the HY AD regime tab.
 */

export interface HyAdPoint {
  date: string;
  net: number;
  cum: number;
  ma21: number | null;
  ma50: number | null;
  spx_close: number | null;
}

export interface HyAdCurrent {
  date: string;
  advances: number;
  declines: number;
  unchanged: number;
  total: number;
  net: number;
  cum: number;
  ma21: number | null;
  ma50: number | null;
}

export interface HyAdData {
  scan_time: string | null;
  data_date: string | null;
  current: HyAdCurrent | null;
  series: HyAdPoint[];
  missing?: boolean;
}

// Contract: absent HYAD data is HTTP 200 with missing:true, never a 4xx.
export const MISSING_HYAD: HyAdData = Object.freeze({
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [] as HyAdPoint[],
});

export type HyAdRegime = "ADVANCING" | "DETERIORATING" | "MIXED";

/**
 * Strict-inequality regime table: cum > ma21 > ma50 is ADVANCING,
 * cum < ma21 < ma50 is DETERIORATING, anything else (any equality, any
 * null or non-finite input) is MIXED.
 */
export function hyAdRegimeLabel(
  cum: number | null | undefined,
  ma21: number | null | undefined,
  ma50: number | null | undefined,
): HyAdRegime {
  if (cum == null || ma21 == null || ma50 == null) return "MIXED";
  if (!Number.isFinite(cum) || !Number.isFinite(ma21) || !Number.isFinite(ma50)) return "MIXED";
  if (cum > ma21 && ma21 > ma50) return "ADVANCING";
  if (cum < ma21 && ma21 < ma50) return "DETERIORATING";
  return "MIXED";
}

export function hyAdRegimeColor(regime: HyAdRegime): string {
  switch (regime) {
    case "ADVANCING": return "var(--positive)";
    case "DETERIORATING": return "var(--negative)";
    case "MIXED": return "var(--text-muted)";
  }
}

/** Signed count with thousands separators: -2535 renders "-2,535", 900 renders "+900". */
export function formatSignedThousands(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  const rounded = Math.round(v);
  const magnitude = Math.abs(rounded).toLocaleString("en-US");
  return `${rounded < 0 ? "-" : "+"}${magnitude}`;
}

/** Unsigned count with thousands separators: 1227 renders "1,227". */
export function formatThousands(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return Math.round(v).toLocaleString("en-US");
}
