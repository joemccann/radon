/* Radon service-worker policy. Authenticated pages and API responses never
 * enter Cache Storage. Icons, images, and the manifest are intercepted.
 * /_next/static/ is not: the document preloads those files.
 */
const RadonSwDecisions = (() => {
  const SW_VERSION = "2026-09-25.1";
  const STATIC_CACHE = `radon-static-${SW_VERSION}`;
  const KNOWN_CACHES = [STATIC_CACHE];
  const PRECACHE_URLS = [
    "/manifest.webmanifest",
    "/icons/icon-192.png",
    "/icons/icon-512.png",
    "/icons/apple-touch-icon.png",
  ];
  const MAX_STATIC_ENTRIES = 300;

  function isStaticAssetPath(pathname) {
    // /_next/static/ stays out of this list. Next preloads those scripts and
    // the Inter font from the document. respondWith drops that preload:
    // Chrome reports a cross-world service worker mismatch and an unused
    // preload. The files are content-hashed and already use the HTTP cache.
    return pathname.startsWith("/icons/")
      || pathname.startsWith("/images/")
      || pathname === "/manifest.webmanifest";
  }

  function classifyRequest(input) {
    if (input.method !== "GET") return "ignore";
    const url = new URL(input.url);
    if (input.origin && url.origin !== input.origin) return "ignore";
    if (url.pathname.startsWith("/api/")
      || url.pathname.startsWith("/_next/data/")
      || url.pathname.startsWith("/ws")
      || url.pathname === "/health"
      || input.mode === "navigate") {
      return "bypass";
    }
    return isStaticAssetPath(url.pathname) ? "static" : "ignore";
  }

  function shouldCacheResponse(response) {
    return Boolean(
      response
      && response.ok
      && response.status === 200
      && response.type !== "opaqueredirect"
      && response.type !== "opaque",
    );
  }

  function selectStaticEvictions(urls, limit) {
    const protectedPaths = new Set(PRECACHE_URLS);
    const evictable = urls.filter((url) => !protectedPaths.has(new URL(url).pathname));
    const surplus = urls.length - limit;
    return surplus > 0 ? evictable.slice(0, Math.min(surplus, evictable.length)) : [];
  }

  return {
    SW_VERSION,
    STATIC_CACHE,
    KNOWN_CACHES,
    PRECACHE_URLS,
    MAX_STATIC_ENTRIES,
    classifyRequest,
    shouldCacheResponse,
    selectStaticEvictions,
  };
})();

if (typeof module !== "undefined") {
  module.exports = RadonSwDecisions;
} else {
  self.RadonSwDecisions = RadonSwDecisions;
}
