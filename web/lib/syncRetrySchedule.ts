/**
 * Bounded retry schedule for `useSyncHook`'s stale-data retry.
 *
 * The retry re-armed on every response at a flat interval, which is safe only
 * while the condition it retries for is transient. It is not: a predicate like
 * `needsCurrentEtSessionRetry` fires on `!data.scan_time`, and a route serving
 * a degraded payload returns exactly that forever. The loop then runs at the
 * flat interval indefinitely, saturates the route's own rate limit, and starves
 * the recovery scan it is waiting for. R-230.
 */

export type RetryScheduleInput = {
  /** The configured first-retry delay. Zero disables retries entirely. */
  baseMs: number;
  /** How many retries have already been issued for this stale streak. */
  attempt: number;
  /** Ceiling for the exponential growth. */
  maxDelayMs: number;
  /** Give up after this many retries; the normal poll interval takes over. */
  maxAttempts: number;
};

/** Delay before the next retry, or `null` when the hook must stop retrying. */
export function resolveRetryDelayMs({
  baseMs,
  attempt,
  maxDelayMs,
  maxAttempts,
}: RetryScheduleInput): number | null {
  if (baseMs <= 0) return null;
  if (attempt >= maxAttempts) return null;
  return Math.min(baseMs * 2 ** attempt, maxDelayMs);
}
