// Request → rate-limit tier classification (demo.radon.run, Phase 5).
//
// Pure mapping consumed by the middleware demo rate-limiter. Tiers + budgets
// are defined in rateLimit.ts (A reads 100/hr, B expensive 10/hr, C mutations
// 5/day, D AI 5/day). Keeping the classifier pure makes the policy a unit test
// instead of a thing you discover in production.

import type { DemoRateTier } from "./rateLimit";

// The three LLM routes — coarse backstop above the per-endpoint AI quota.
const AI_PATHS = new Set<string>([
  "/api/assistant",
  "/api/ticker/seasonality",
  "/api/ticker/info",
]);

// Expensive scans / aggregations / IB-touching exec, regardless of method.
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

/**
 * Map an API request to its rate-limit tier. Order matters: AI routes (some are
 * GET) are classified before the generic read bucket, and expensive scans
 * (POST) before the generic mutation bucket.
 */
export function classifyRateTier(
  method: string,
  pathname: string,
): DemoRateTier {
  if (AI_PATHS.has(pathname)) return "D";
  if (EXPENSIVE_PATTERNS.some((re) => re.test(pathname))) return "B";
  if (WRITE_METHODS.has(method.toUpperCase())) return "C";
  return "A";
}
