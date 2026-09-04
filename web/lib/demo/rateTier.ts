// Request → rate-limit tier classification (demo.radon.run, Phase 5).
//
// Pure mapping consumed by the middleware demo rate-limiter. Tiers + budgets
// are defined in rateLimit.ts (A reads 100/hr, B expensive 10/hr, C mutations
// 5/day, D AI 5/day, E/F WS reconnects, G/H headline polling, I/J shell
// polling). Keeping the classifier pure makes the policy a unit test instead
// of a thing you discover in production.

import type { DemoRateTier } from "./rateLimit";

// The three LLM routes — coarse backstop above the per-endpoint AI quota.
const AI_PATHS = new Set<string>([
  "/api/assistant",
  "/api/ticker/seasonality",
  "/api/ticker/info",
]);

// Expensive producers / aggregations / IB-touching exec. Cached GETs are
// ordinary reads; classifying them here made passive panels spend a 10/hour
// producer allowance before an operator touched a control.
const EXPENSIVE_PATTERNS: RegExp[] = [
  /^\/api\/(vcg|gex|cri|leap)\/scan$/,
  /^\/api\/regime(\/|$)/,
  /^\/api\/performance(\/|$)/,
  /^\/api\/scanner(\/|$)/,
  /^\/api\/discover(\/|$)/,
  /^\/api\/evaluate(\/|$)/,
  /^\/api\/pi(\/|$)/,
];

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// These GETs are automatic workstation refreshes, not operator actions. Their
// shipped cadence is much higher than tier A's 100/hour (the three futures
// fallbacks alone issue six requests/minute), so they need a bounded budget
// that matches the UI instead of consuming every ordinary read token.
const PASSIVE_POLL_PATHS = new Set<string>([
  "/api/flex-token",
  "/api/futures-quote",
  "/api/orders",
  "/api/portfolio",
  "/api/risk-free-rate",
  "/api/service-health",
]);

/**
 * Map an API request to its rate-limit tier. Order matters: AI routes (some are
 * GET) are classified before the generic read bucket, and expensive scans
 * (POST) before the generic mutation bucket.
 */
export function classifyRateTier(
  method: string,
  pathname: string,
): DemoRateTier {
  const normalizedMethod = method.toUpperCase();
  if (normalizedMethod === "POST" && pathname === "/api/ib/ws-ticket") return "E";
  if (normalizedMethod === "GET" && pathname === "/api/headlines") return "G";
  if (normalizedMethod === "GET" && PASSIVE_POLL_PATHS.has(pathname)) return "I";
  if (AI_PATHS.has(pathname)) return "D";
  if (normalizedMethod !== "GET" && EXPENSIVE_PATTERNS.some((re) => re.test(pathname))) return "B";
  if (WRITE_METHODS.has(normalizedMethod)) return "C";
  return "A";
}

/**
 * Cheap and expensive resources receive independent per-user budgets. A busy
 * dashboard may exhaust its own regime allowance, but cannot spend the first
 * scanner visit's allowance. Grouping on the first API segment keeps nested
 * routes together so changing a suffix cannot evade the cap.
 */
export function demoRateLimitKey(
  tier: DemoRateTier,
  userId: string,
  pathname: string,
): string {
  if (tier !== "A" && tier !== "B") return userId;
  const resource = /^\/api\/([^/]+)/.exec(pathname)?.[1] ?? "api";
  return `${userId}:resource:${resource}`;
}
