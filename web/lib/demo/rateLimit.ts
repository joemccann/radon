// Tiered Upstash sliding-window rate limiter, keyed by Clerk userId
// (demo.radon.run, plan §Guardrails — Rate-limit / DOS).
//
// Four tiers:
//   A — reads        ~100/hr   (cheap GETs)
//   B — expensive    ~10/hr    (scans, heavy aggregations)
//   C — mutations    5/day     (writes: notes, watchlist, alerts)
//   D — AI           5/day     (LLM routes; the per-endpoint quota is the
//                               finer-grained backstop in aiQuota.ts)
//
// The limiter is constructed LAZILY from UPSTASH_REDIS_REST_URL / _TOKEN.
// Production and demo deployments fail closed if it is unavailable; local
// development/test keeps a no-op result so isolated route tests remain usable.
//
// Server util — not declared Edge, but kept free of node:* so a future Edge
// caller works.

import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

export type DemoRateTier = "A" | "B" | "C" | "D" | "E" | "F";

export type DemoRateLimitResult = {
  success: boolean;
  limit: number;
  remaining: number;
  reset: number;
};

type TierConfig = { limit: number; window: Parameters<typeof Ratelimit.slidingWindow>[1] };

const TIER_CONFIG: Record<DemoRateTier, TierConfig> = {
  A: { limit: 100, window: "1 h" },
  B: { limit: 10, window: "1 h" },
  C: { limit: 5, window: "1 d" },
  D: { limit: 5, window: "1 d" },
  E: { limit: 20, window: "1 m" },
  F: { limit: 200, window: "1 d" },
};

// Generous no-op result for builds without Upstash configured.
function allowAll(tier: DemoRateTier): DemoRateLimitResult {
  const { limit } = TIER_CONFIG[tier];
  return { success: true, limit, remaining: limit, reset: 0 };
}

function denyUnavailable(tier: DemoRateTier): DemoRateLimitResult {
  const { limit } = TIER_CONFIG[tier];
  return { success: false, limit, remaining: 0, reset: 0 };
}

let _redis: Redis | null | undefined; // undefined = not yet resolved
const _limiters = new Map<DemoRateTier, Ratelimit>();

function getRedis(): Redis | null {
  if (_redis !== undefined) return _redis;
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  _redis = url && token ? new Redis({ url, token }) : null;
  return _redis;
}

function getLimiter(tier: DemoRateTier): Ratelimit | null {
  const redis = getRedis();
  if (!redis) return null;
  const existing = _limiters.get(tier);
  if (existing) return existing;
  const { limit, window } = TIER_CONFIG[tier];
  const limiter = new Ratelimit({
    redis,
    limiter: Ratelimit.slidingWindow(limit, window),
    prefix: `radon:demo:rl:${tier}`,
    analytics: false,
  });
  _limiters.set(tier, limiter);
  return limiter;
}

/**
 * Consume one token for `userId` in `tier`. Returns `{ success }` plus the
 * remaining budget. No-ops to allow when Upstash is unconfigured.
 */
export async function demoRateLimit(
  tier: DemoRateTier,
  userId: string,
): Promise<DemoRateLimitResult> {
  const limiter = getLimiter(tier);
  if (!limiter) {
    return process.env.NODE_ENV === "production" || process.env.NEXT_PUBLIC_RADON_DEMO === "1"
      ? denyUnavailable(tier)
      : allowAll(tier);
  }
  const { success, limit, remaining, reset } = await limiter.limit(userId);
  return { success, limit, remaining, reset };
}

// Test seam — drop memoised redis/limiters so env changes take effect.
export function __resetRateLimitForTests(): void {
  _redis = undefined;
  _limiters.clear();
}
