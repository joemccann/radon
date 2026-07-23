/* Radon SW decision module — pure logic only, zero DOM/SW API access.
 * Loaded by sw.js via importScripts("/sw-decisions.js") and pinned by
 * web/tests/sw-decisions.test.ts (vm-evaluated; web/ is "type":"module" so
 * this classic script keeps a UMD-style guard instead of ESM exports).
 *
 * Cache contract: every classification here feeds a NETWORK-FIRST handler.
 * Cache Storage is read only when fetch() rejects; nothing in this module may
 * make an online response come from cache.
 */
const RadonSwDecisions = (() => {
  // Cache-schema version, folded into the pages/api cache NAMES: bumping it
  // makes activate drop the old-format entries (they fall out of
  // KNOWN_CACHES). An unreferenced version constant invalidates nothing.
  const SW_VERSION = "2026-07-22.1";

  // radon-static-v1 is the renamed radon-shell-v1 (behavior unchanged:
  // cache-first hashed chunks/icons). Deliberately NOT version-bumped per
  // deploy: cached offline HTML must keep finding its old hashed chunks here.
  // Growth is bounded by MAX_STATIC_ENTRIES instead (precache never evicted).
  const STATIC_CACHE = "radon-static-v1";
  const PAGES_CACHE = "radon-pages-" + SW_VERSION;
  const API_CACHE = "radon-api-" + SW_VERSION;
  const KNOWN_CACHES = [STATIC_CACHE, PAGES_CACHE, API_CACHE];

  const PRECACHE_URLS = [
    "/manifest.webmanifest",
    "/icons/icon-192.png",
    "/icons/icon-512.png",
    "/icons/apple-touch-icon.png",
    "/offline.html",
  ];

  const MAX_PAGE_ENTRIES = 30;
  const MAX_API_ENTRIES = 100;
  const MAX_STATIC_ENTRIES = 300;

  // Same-origin /api GETs eligible for offline fallback. Exact-pathname match
  // only — /api/orders/place, /api/prices, /api/admin/* etc. stay untouched.
  const OFFLINE_API_PATHS = [
    "/api/portfolio",
    "/api/orders",
    "/api/journal",
    "/api/scanner",
    "/api/discover",
    "/api/leap",
    "/api/watchlist",
    "/api/newsfeed/posts",
    "/api/ticker/info",
    "/api/options/expirations",
    "/api/options/chain",
    "/api/service-health",
    "/api/regime",
    "/api/vcg",
    "/api/gex",
    "/api/blotter",
    "/api/internals",
    "/api/cash-flows",
  ];

  function isOfflineCacheableApi(pathname) {
    return OFFLINE_API_PATHS.indexOf(pathname) !== -1;
  }

  function isBypassedPath(pathname) {
    return (
      pathname.startsWith("/_next/data/") ||
      pathname.startsWith("/ws") ||
      pathname === "/health"
    );
  }

  function isStaticAssetPath(pathname) {
    return (
      pathname.startsWith("/_next/static/") ||
      pathname.startsWith("/icons/") ||
      pathname.startsWith("/images/") ||
      pathname === "/manifest.webmanifest"
    );
  }

  /**
   * classifyRequest({ method, url, mode, origin }) →
   *   "ignore"     — no respondWith, not part of the SW's surface
   *   "bypass"     — no respondWith by contract (/ws, /health, /_next/data, non-allowlisted /api)
   *   "static"     — cache-first static asset
   *   "api"        — network-first allowlisted API GET
   *   "navigation" — network-first HTML navigation (request.mode === "navigate" only;
   *                  RSC soft-nav fetches stay untouched so cors page fetches → ignore)
   */
  function classifyRequest(input) {
    if (input.method !== "GET") return "ignore";
    const url = new URL(input.url);
    if (input.origin && url.origin !== input.origin) return "ignore";
    if (isBypassedPath(url.pathname)) return "bypass";
    if (url.pathname.startsWith("/api/")) {
      return isOfflineCacheableApi(url.pathname) ? "api" : "bypass";
    }
    if (isStaticAssetPath(url.pathname)) return "static";
    if (input.mode === "navigate") return "navigation";
    return "ignore";
  }

  // Routes that serve 200 + structurally-valid-but-DEGRADED bodies on
  // failure (feedback_http_status_for_real_errors). A degraded body passing
  // the generic non-empty check would OVERWRITE the last-known-good entry
  // and offline would replay the degraded payload — the exact failure the
  // cache exists to prevent. Validators return true only for real payloads.
  const API_BODY_VALIDATORS = {
    "/api/scanner": (p) => typeof p.scan_time === "string" && p.scan_time.length > 0,
    "/api/ticker/info": (p) =>
      p.uw_info != null && typeof p.uw_info === "object" && Object.keys(p.uw_info).length > 0,
  };

  // Never cache empty results: only a non-empty JSON object/array is a payload
  // worth replaying offline. `missing: true` is the house 200-degraded marker
  // (feedback_http_status_for_real_errors) — never a last-known-good payload.
  function isCacheworthyApiBody(text, pathname) {
    if (!text) return false;
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      return false;
    }
    if (parsed === null || typeof parsed !== "object") return false;
    if (Array.isArray(parsed)) return parsed.length > 0;
    if (Object.keys(parsed).length === 0) return false;
    if (parsed.missing === true) return false;
    const validator = pathname ? API_BODY_VALIDATORS[pathname] : undefined;
    return validator ? validator(parsed) === true : true;
  }

  // Only a clean 200 enters Cache Storage. 4xx/5xx/304/redirects pass through
  // untouched (the auth perimeter's 401/404 must never be masked by cache).
  // Falsy input → false, never a throw (a throw here escapes the handler as
  // an unhandled rejection).
  function shouldCacheResponse(res) {
    if (!res || !res.ok || res.status !== 200) return false;
    if (res.type === "opaqueredirect" || res.type === "opaque") return false;
    return true;
  }

  // Pages cache is keyed by pathname only so /dashboard?x=1 refreshes the same
  // entry that an offline /dashboard load reads.
  function navigationCacheKey(urlString) {
    return new URL(urlString).pathname;
  }

  // Oldest-first surplus over the limit. entries: [{ url, cachedAt }].
  // Missing/garbage stamps sort as -Infinity (evict first) — Date.parse on a
  // non-string is engine-dependent, never rely on it.
  function evictionTime(entry) {
    if (typeof entry.cachedAt !== "string" || entry.cachedAt === "") return -Infinity;
    const ms = Date.parse(entry.cachedAt);
    return Number.isFinite(ms) ? ms : -Infinity;
  }

  function selectEvictions(entries, limit) {
    if (entries.length <= limit) return [];
    const sorted = entries.slice().sort((a, b) => evictionTime(a) - evictionTime(b));
    return sorted.slice(0, entries.length - limit).map((entry) => entry.url);
  }

  // Static entries carry no cachedAt stamp; bound by count in insertion
  // order, never evicting the precached shell (offline.html, manifest, icons).
  function selectStaticEvictions(urls, limit) {
    const evictable = urls.filter((u) => {
      const pathname = new URL(u).pathname;
      return PRECACHE_URLS.indexOf(pathname) === -1;
    });
    const surplus = urls.length - limit;
    if (surplus <= 0) return [];
    return evictable.slice(0, Math.min(surplus, evictable.length));
  }

  function buildCachedAtStamp(nowMs) {
    return { "X-Radon-Cached-At": new Date(nowMs).toISOString() };
  }

  function buildOfflineServeHeaders(headersObj) {
    const out = {};
    for (const key in headersObj) out[key] = headersObj[key];
    out["X-Radon-Offline"] = "1";
    return out;
  }

  return {
    SW_VERSION,
    STATIC_CACHE,
    PAGES_CACHE,
    API_CACHE,
    KNOWN_CACHES,
    PRECACHE_URLS,
    OFFLINE_API_PATHS,
    MAX_PAGE_ENTRIES,
    MAX_API_ENTRIES,
    MAX_STATIC_ENTRIES,
    classifyRequest,
    isOfflineCacheableApi,
    shouldCacheResponse,
    isCacheworthyApiBody,
    navigationCacheKey,
    selectEvictions,
    selectStaticEvictions,
    buildCachedAtStamp,
    buildOfflineServeHeaders,
  };
})();

if (typeof module !== "undefined") {
  module.exports = RadonSwDecisions;
} else {
  self.RadonSwDecisions = RadonSwDecisions;
}
