export type CtaStaleReason = "fresh" | "behind_target" | "missing_cache";

export interface CtaCacheMeta {
  last_refresh: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_threshold_seconds: number | null;
  target_date: string;
  expected_date?: string | null;
  latest_cache_date: string | null;
  latest_available_date?: string | null;
  stale_reason: CtaStaleReason;
}
