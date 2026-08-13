/* Radon service worker. Only public static assets are cached. Authenticated
 * navigations, RSC payloads, API responses, and WebSocket traffic bypass it.
 */
importScripts("/sw-decisions.js");

const D = self.RadonSwDecisions;
let purgeGeneration = 0;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(D.STATIC_CACHE).then((cache) => cache.addAll(D.PRECACHE_URLS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => D.KNOWN_CACHES.indexOf(key) === -1).map((key) => caches.delete(key)),
  )));
  self.clients.claim();
});

self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "radon-clear-caches") return;
  purgeGeneration += 1;
  const purge = caches.keys().then((keys) => Promise.all(
    keys.filter((key) => key.startsWith("radon-pages-") || key.startsWith("radon-api-"))
      .map((key) => caches.delete(key)),
  ));
  if (event.waitUntil) event.waitUntil(purge);
});

async function sweepStaticCache(cache) {
  const keys = await cache.keys();
  const urls = keys.map((request) => request.url);
  for (const url of D.selectStaticEvictions(urls, D.MAX_STATIC_ENTRIES)) {
    await cache.delete(url);
  }
}

async function handleStatic(event) {
  const generation = purgeGeneration;
  const request = event.request;
  const cache = await caches.open(D.STATIC_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (generation === purgeGeneration && D.shouldCacheResponse(response)) {
    event.waitUntil(cache.put(request, response.clone()).then(() => sweepStaticCache(cache)).catch(() => {}));
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
  if (kind === "static") event.respondWith(handleStatic(event));
});
