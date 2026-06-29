/**
 * Lightweight per-IP fixed-window rate limiter.
 *
 * Best-effort, per-instance (in-memory) throttling — a CDN/Caddy limiter
 * is the durable layer. This defends against runaway callers on a single
 * Next.js worker; it does NOT coordinate across multiple server instances.
 */

interface Window {
  count: number;
  resetAt: number;
}

// Module-level store — survives across requests within one Node.js worker.
const store = new Map<string, Window>();

// Cap the number of distinct keys to prevent unbounded Map growth.
const MAX_KEYS = 4_000;

function evictStale(now: number): void {
  for (const [key, win] of store) {
    if (win.resetAt <= now) store.delete(key);
  }
}

function evictOldest(): void {
  const firstKey = store.keys().next().value;
  if (firstKey !== undefined) store.delete(firstKey);
}

export interface RateLimitResult {
  ok: boolean;
  retryAfterSec: number;
}

/**
 * Check whether `key` is within the fixed window.
 * Mutates the store on every call.
 */
export function rateLimit(
  key: string,
  { limit, windowMs }: { limit: number; windowMs: number },
): RateLimitResult {
  const now = Date.now();

  // Lazy stale eviction — run every ~100 calls by checking store size.
  if (store.size >= MAX_KEYS) {
    evictStale(now);
    if (store.size >= MAX_KEYS) evictOldest();
  }

  const existing = store.get(key);
  const windowExpired = !existing || existing.resetAt <= now;

  if (windowExpired) {
    store.set(key, { count: 1, resetAt: now + windowMs });
    return { ok: true, retryAfterSec: 0 };
  }

  existing.count += 1;

  if (existing.count > limit) {
    const retryAfterSec = Math.ceil((existing.resetAt - now) / 1000);
    return { ok: false, retryAfterSec };
  }

  return { ok: true, retryAfterSec: 0 };
}

/**
 * Read the client IP from a Request.
 * Prefers the first hop in `x-forwarded-for` (set by Caddy),
 * falls back to a sentinel so the limiter still works — including when the
 * handler is invoked without a Request (e.g. unit tests call `POST()` with no
 * args), which must degrade to the sentinel rather than throw.
 */
export function clientIp(request?: Request): string {
  const xff = request?.headers?.get("x-forwarded-for");
  if (xff) {
    const first = xff.split(",")[0].trim();
    if (first) return first;
  }
  return "unknown";
}

/** Rate-limit constants */
export const SHARE_CARD_LIMIT = 30;   // POST share generators: 30 req / 60 s
export const SHARE_PNL_LIMIT = 60;    // GET share/pnl (OG image): 60 req / 60 s
export const SHARE_WINDOW_MS = 60_000; // 60-second fixed window
