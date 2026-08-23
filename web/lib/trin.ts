/**
 * TRIN (NYSE Arms Index) on 60-minute bars with MA(10). Payload types + pure
 * helpers for the TRIN regime tab. Low TRIN (below ZONE_LOW) flags a buying
 * climax zone; elevated TRIN flags washout selling.
 */

export const ZONE_LOW = 0.6;
export const ZONE_NEAR = 0.65;
export const ZONE_HIGH = 1.5;

export type TrinState = "in_zone" | "near_zone" | "elevated" | "neutral";

export interface TrinHourlyBar {
  ts: string;
  bucket: string;
  trin: number;
  ma10: number | null;
}

export interface TrinDailyPoint {
  date: string;
  close: number;
}

export interface TrinCurrent {
  ts: string | null;
  session_date: string | null;
  trin: number | null;
  ma10: number | null;
  state: TrinState;
  adv: number | null;
  dec: number | null;
  up_vol: number | null;
  down_vol: number | null;
  daily_close: number | null;
  daily_date: string | null;
  zone_low: number;
  zone_near: number;
  zone_high: number;
}

export interface TrinData {
  scan_time: string | null;
  source: string | null;
  current: TrinCurrent | null;
  hourly: TrinHourlyBar[];
  daily: TrinDailyPoint[];
  missing?: boolean;
}

// Contract: absent TRIN data is HTTP 200 with missing:true, never a 4xx.
export const MISSING_TRIN: TrinData = Object.freeze({
  missing: true,
  scan_time: null,
  source: null,
  current: null,
  hourly: [] as TrinHourlyBar[],
  daily: [] as TrinDailyPoint[],
});

export function formatTrin(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

export function stateLabel(state: TrinState | null | undefined): string {
  if (state === "in_zone") return "IN ZONE";
  if (state === "near_zone") return "NEAR ZONE";
  if (state === "elevated") return "ELEVATED";
  if (state === "neutral") return "NEUTRAL";
  return "---";
}

export type TrinTone = "positive" | "negative" | "muted";

export function stateTone(state: TrinState | null | undefined): TrinTone {
  if (state === "in_zone" || state === "near_zone") return "negative";
  if (state === "elevated") return "positive";
  return "muted";
}
