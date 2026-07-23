/* Radon service worker — network-first everywhere it intercepts; Cache
 * Storage is read ONLY when fetch() rejects (offline). Online responses are
 * returned untouched — the Next.js cache contract (force-dynamic + no-store)
 * governs the HTTP layer and is never bypassed. Cached offline HTML pairs
 * with old hashed chunks retained in radon-static-v1 (cache-first, not
 * version-bumped per deploy). Decision logic + constants live in
 * sw-decisions.js; behavior is pinned by web/tests/sw-smoke.test.ts.
 */
importScripts("/sw-decisions.js");

const D = self.RadonSwDecisions;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(D.STATIC_CACHE).then((cache) => cache.addAll(D.PRECACHE_URLS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => D.KNOWN_CACHES.indexOf(k) === -1).map((k) => caches.delete(k)),
    )),
  );
  self.clients.claim();
});

// Sign-out purge: drops user data (pages + API payloads), keeps the static shell.
self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "radon-clear-caches") return;
  const purge = Promise.all([
    caches.delete(D.PAGES_CACHE),
    caches.delete(D.API_CACHE),
  ]);
  if (event.waitUntil) event.waitUntil(purge);
});

/* Same-pair reassembly rule: a stored/served Response is rebuilt ONLY from a
 * single response's own body + status + headers plus the extra stamp headers.
 * Never mix headers/body across responses and never synthesize HTML — cached
 * navigations must replay their own per-request CSP nonce self-consistently.
 */
async function stampForCache(sourceBody, source, nowMs) {
  const headers = new Headers(source.headers);
  const stamp = D.buildCachedAtStamp(nowMs);
  for (const key in stamp) headers.set(key, stamp[key]);
  return new Response(sourceBody, {
    status: source.status,
    statusText: source.statusText,
    headers,
  });
}

async function serveFromCache(cached) {
  const headersObj = {};
  cached.headers.forEach((value, key) => {
    headersObj[key] = value;
  });
  const body = await cached.arrayBuffer();
  return new Response(body, {
    status: cached.status,
    statusText: cached.statusText,
    headers: D.buildOfflineServeHeaders(headersObj),
  });
}

async function sweepCache(cache, limit) {
  const keys = await cache.keys();
  if (keys.length <= limit) return;
  const entries = [];
  for (const request of keys) {
    const cached = await cache.match(request);
    entries.push({
      url: request.url,
      cachedAt: (cached && cached.headers.get("X-Radon-Cached-At")) || "",
    });
  }
  for (const url of D.selectEvictions(entries, limit)) {
    await cache.delete(url);
  }
}

async function putStampedPage(key, clone) {
  const body = await clone.arrayBuffer();
  const stamped = await stampForCache(body, clone, Date.now());
  const cache = await caches.open(D.PAGES_CACHE);
  await cache.put(key, stamped);
  await sweepCache(cache, D.MAX_PAGE_ENTRIES);
}

async function putStampedApi(key, clone) {
  const text = await clone.text();
  if (!D.isCacheworthyApiBody(text, new URL(key).pathname)) return;
  const stamped = await stampForCache(text, clone, Date.now());
  const cache = await caches.open(D.API_CACHE);
  await cache.put(key, stamped);
  await sweepCache(cache, D.MAX_API_ENTRIES);
}

async function handleNavigation(event) {
  const request = event.request;
  const key = D.navigationCacheKey(request.url);
  try {
    const response = await fetch(request);
    if (D.shouldCacheResponse(response)) {
      event.waitUntil(putStampedPage(key, response.clone()));
    }
    return response;
  } catch (err) {
    const cache = await caches.open(D.PAGES_CACHE);
    const cached = await cache.match(key);
    if (cached) return serveFromCache(cached);
    // Last-resort fallback carries the same offline markers as any other
    // offline-served response so client logic keying on the header sees it.
    const fallback = await caches.match("/offline.html");
    if (fallback) return serveFromCache(fallback);
    throw err;
  }
}

async function handleApiGet(event) {
  const request = event.request;
  try {
    const response = await fetch(request);
    if (D.shouldCacheResponse(response)) {
      event.waitUntil(putStampedApi(request.url, response.clone()));
    }
    return response;
  } catch (err) {
    const cache = await caches.open(D.API_CACHE);
    const cached = await cache.match(request.url);
    if (cached) return serveFromCache(cached);
    throw err;
  }
}

async function sweepStaticCache(cache) {
  const keys = await cache.keys();
  if (keys.length <= D.MAX_STATIC_ENTRIES) return;
  const urls = keys.map((request) => request.url);
  for (const url of D.selectStaticEvictions(urls, D.MAX_STATIC_ENTRIES)) {
    await cache.delete(url);
  }
}

async function handleStatic(event) {
  const request = event.request;
  const cache = await caches.open(D.STATIC_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (D.shouldCacheResponse(response)) {
    event.waitUntil(
      cache
        .put(request, response.clone())
        .then(() => sweepStaticCache(cache))
        .catch(() => {}),
    );
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const kind = D.classifyRequest({
    method: request.method,
    url: request.url,
    mode: request.mode,
    origin: self.location.origin,
  });

  switch (kind) {
    case "static":
      event.respondWith(handleStatic(event));
      return;
    case "navigation":
      event.respondWith(handleNavigation(event));
      return;
    case "api":
      event.respondWith(handleApiGet(event));
      return;
    default:
      // "ignore" / "bypass": no respondWith — pure browser-network pass-through.
      return;
  }
});
