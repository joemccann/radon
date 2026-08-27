/**
 * R-230: the stale-data retry must cap and back off.
 *
 * `needsCurrentEtSessionRetry` returns true unconditionally on
 * `!data?.scan_time`, and the fabricated dead-feed payload carried
 * `scan_time: ""`. Wired to `retryIntervalMs: 5000` with `retryMethod: "GET"`,
 * the hook dropped from its 60 s interval to a 5 s GET loop that re-armed
 * after every response — no cap, no backoff — because the response it was
 * retrying was precisely the one that could never satisfy the predicate.
 * That is 12 req/min per tab against a route limited to 20/min, so two tabs
 * saturated the bucket and 429'd the regime route for the whole user, and
 * every stale GET also kicked triggerBackgroundScan(). The busier the loop,
 * the less likely the recovering scan got through.
 */

import { describe, it, expect } from "vitest";
import { resolveRetryDelayMs } from "../lib/syncRetrySchedule";
import { REGIME_SYNC_CONFIG } from "../lib/useRegime";

describe("resolveRetryDelayMs", () => {
  const base = 5000;
  const maxDelay = 60_000;
  const maxAttempts = 6;

  it("keeps the first retry at the configured interval", () => {
    expect(resolveRetryDelayMs({ baseMs: base, attempt: 0, maxDelayMs: maxDelay, maxAttempts })).toBe(5000);
  });

  it("backs off exponentially", () => {
    const delays = [0, 1, 2, 3].map((attempt) =>
      resolveRetryDelayMs({ baseMs: base, attempt, maxDelayMs: maxDelay, maxAttempts }),
    );
    expect(delays).toEqual([5000, 10_000, 20_000, 40_000]);
  });

  it("clamps at the ceiling instead of growing without bound", () => {
    expect(resolveRetryDelayMs({ baseMs: base, attempt: 10, maxDelayMs: maxDelay, maxAttempts: 99 })).toBe(60_000);
  });

  it("stops retrying once the attempt cap is reached", () => {
    expect(resolveRetryDelayMs({ baseMs: base, attempt: maxAttempts, maxDelayMs: maxDelay, maxAttempts })).toBeNull();
  });

  it("never retries when no interval is configured", () => {
    expect(resolveRetryDelayMs({ baseMs: 0, attempt: 0, maxDelayMs: maxDelay, maxAttempts })).toBeNull();
  });

  it("bounds a full retry sequence well under the route's rate limit", () => {
    // /api/regime allows 20 req/min. Sum the whole ladder and assert the
    // hook cannot issue more than a handful of GETs before giving up.
    let attempt = 0;
    let issued = 0;
    let elapsed = 0;
    for (;;) {
      const delay = resolveRetryDelayMs({ baseMs: base, attempt, maxDelayMs: maxDelay, maxAttempts });
      if (delay === null) break;
      elapsed += delay;
      issued += 1;
      attempt += 1;
    }
    expect(issued).toBe(maxAttempts);
    // The old loop issued 12/min forever; this issues 6 total over minutes.
    expect(elapsed).toBeGreaterThan(120_000);
  });
});

describe("REGIME_SYNC_CONFIG", () => {
  it("carries a retry cap and a backoff ceiling", () => {
    expect(REGIME_SYNC_CONFIG.maxRetryAttempts).toBeGreaterThan(0);
    expect(REGIME_SYNC_CONFIG.maxRetryDelayMs).toBeGreaterThan(REGIME_SYNC_CONFIG.retryIntervalMs);
  });

  it("does not treat a payload marked missing as retry-forever", () => {
    // The predicate itself still fires — the cap is what bounds it — but a
    // payload the route has explicitly marked unavailable must not look like
    // a merely-stale one that a fast GET could fix.
    expect(REGIME_SYNC_CONFIG.maxRetryAttempts).toBeLessThanOrEqual(10);
  });
});
