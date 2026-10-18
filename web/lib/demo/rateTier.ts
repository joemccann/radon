// Request → rate-limit tier classification (demo.radon.run, Phase 5).
//
// Pure mapping consumed by the middleware demo rate-limiter. Tiers + budgets
// are defined in rateLimit.ts (A reads 100/hr, B expensive 10/hr, C mutations
// 5/day, D AI 5/day, E/F WS reconnects, G/H headline polling, I/J shell
// polling). Keeping the classifier pure makes the policy a unit test instead
// of a thing you discover in production.

import type { DemoRateTier } from "./rateLimit";

// Every route that can reach an LLM provider — the coarse SHARED backstop above
// the per-endpoint AI quota. A route's own `durableRateTier` guard is keyed on
// `route:<key>:<user>`, so it is an independent budget: a route missing from
// this set gets its own daily allowance on top of the documented shared AI
// ceiling, against production provider keys. Drift pin:
// demo-ai-budget-route-coverage.test.ts walks the real import graph and fails
// when a new LLM route ships unlisted (DS-2026-10-18-03).
const AI_PATHS = new Set<string>([
  "/api/assistant",
  "/api/newsfeed/explain",
  "/api/newsfeed/share",
  "/api/ticker/seasonality",
  "/api/ticker/info",
]);

// Producer mutations: non-GET requests that recompute, scan or render on the
// server. Derived from the real route tree, where each of these handlers
// declares `durableRateTier: "B"` on its own guard. Cached GETs are ordinary
// reads; classifying them here made passive panels spend a 10/hour producer
// allowance before an operator touched a control. Drift pin:
// demo-rate-tier-route-coverage.test.ts runs every mutating route on disk
// through this classifier, so a new producer cannot land in tier C (the
// 5/day budget it would share with paper orders).
const EXPENSIVE_PATTERNS: RegExp[] = [
  /^\/api\/(breadth|discover|gamma-rotation|gex|internals|regime|scanner)$/,
  /^\/api\/(gex|internals|regime|vcg|menthorq\/cta)\/share$/,
  /^\/api\/scanner\/(bounce|strength|theta|vol-skew-mr)\/scan$/,
  /^\/api\/(garch-convergence|leap)\/scan$/,
  /^\/api\/flow-analysis\/[^/]+$/,
  /^\/api\/options\/rv-ratio$/,
  /^\/api\/knowledge\/search$/,
];

// Reads sent as POST because the request carries a bounded symbol batch.
const READ_POST_PATHS = new Set<string>(["/api/previous-close"]);

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
  if (normalizedMethod === "POST" && READ_POST_PATHS.has(pathname)) return "A";
  if (WRITE_METHODS.has(normalizedMethod)) return "C";
  return "A";
}

// Every real first segment under web/app/api. The classifier runs in
// middleware, BEFORE routing, on the raw pathname — without this allowlist a
// nonsense segment (`/api/aaa1`, `/api/aaa2`, ...) minted an unbounded stream
// of fresh per-resource tier-A budgets (R-652). Drift pin:
// rel244-demo-rate-budget.test.ts fails when a new segment ships unlisted.
export const KNOWN_API_SEGMENTS: ReadonlySet<string> = new Set([
  "admin", "ai-cycle", "alerts", "assistant", "attribution", "backtest", "blotter",
  "bookmarks", "bpi", "breadth", "calm-streak", "cash-flows", "catalysts", "cor",
  "credentials", "credit-spread", "credit-vix", "discover", "dispersion", "divyield",
  "equibles-ats-venue-share", "equibles-cot-positioning",
  "equibles-filing-forensics", "equibles-short-crowding",
  "equibles-smart-money-13f", "flex-token", "flow-analysis", "flow-surprise",
  "futures", "futures-quote", "gamma-rotation", "garch-convergence", "gex",
  "headlines", "hhlev", "hyad", "ib", "iei-hyg", "index-options",
  "index-quote", "informed-flow", "internals", "iv-spread", "ivrank",
  "journal", "knowledge", "leap", "llm-token-index", "ma-ratio", "rsi-oversold",
  "margin-debt", "menthorq", "models", "newsfeed", "options", "orders",
  "paper", "performance", "pi", "portfolio", "preferences", "previous-close",
  "prices", "probe", "profile", "record-high-breadth", "regime", "research", "risk-free-rate", "scanner",
  "service-health", "setup", "share", "short-availability", "skew", "skew2d",
  "straddle", "streaks", "ticker", "trin", "vcg", "version", "vix-pc", "vixcor", "vixts", "panic-index",
  "vol-cone", "watchlist", "webhooks", "yield-curve",
]);

/**
 * Cheap and expensive resources receive independent per-user budgets. A busy
 * dashboard may exhaust its own regime allowance, but cannot spend the first
 * scanner visit's allowance. Grouping on the first API segment keeps nested
 * routes together so changing a suffix cannot evade the cap. Segments not on
 * the route allowlist collapse to ONE shared bucket per user so invented
 * paths cannot mint fresh allowances (R-652).
 */
export function demoRateLimitKey(
  tier: DemoRateTier,
  userId: string,
  pathname: string,
): string {
  if (tier !== "A" && tier !== "B") return userId;
  const segment = /^\/api\/([^/]+)/.exec(pathname)?.[1];
  const resource = segment && KNOWN_API_SEGMENTS.has(segment) ? segment : "other";
  return `${userId}:resource:${resource}`;
}
