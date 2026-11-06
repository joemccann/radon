/** Freshness block for a snapshot route, derived from the snapshot's own content time. */
export interface SnapshotCacheMeta {
  last_refresh: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_threshold_seconds: number;
}

/** An unknown timestamp is stale by construction: it can never read as current. */
export function snapshotCacheMeta(
  timestampMs: number | null,
  staleThresholdSeconds: number,
): SnapshotCacheMeta {
  if (timestampMs === null) {
    return {
      last_refresh: null,
      age_seconds: null,
      is_stale: true,
      stale_threshold_seconds: staleThresholdSeconds,
    };
  }
  const ageSeconds = (Date.now() - timestampMs) / 1000;
  return {
    last_refresh: new Date(timestampMs).toISOString(),
    age_seconds: Math.max(0, Math.round(ageSeconds)),
    is_stale: ageSeconds > staleThresholdSeconds,
    stale_threshold_seconds: staleThresholdSeconds,
  };
}
