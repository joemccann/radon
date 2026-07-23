/**
 * Pins web/public/sw-decisions.js — the pure decision module the service
 * worker loads via importScripts("/sw-decisions.js"). Classic script with a
 * UMD-style guard, so the test evaluates it in a vm sandbox with a CommonJS
 * `module` object (web/package.json is "type":"module", which makes a direct
 * ESM import of a classic CJS-guarded script impossible).
 *
 * Style: fixture factory + constant pinning per depth-derivations.test.ts /
 * staleDataMachine.test.js. Fixed literal timestamps only — every time-based
 * function takes an injected now.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve, dirname } from "node:path";
import vm from "node:vm";

const DECISIONS_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../public/sw-decisions.js",
);

function loadDecisions() {
  const src = readFileSync(DECISIONS_PATH, "utf8");
  const sandbox: Record<string, unknown> = { module: { exports: {} }, URL };
  vm.runInNewContext(src, sandbox);
  return (sandbox.module as { exports: Record<string, any> }).exports;
}

const D = loadDecisions();

const ORIGIN = "https://app.radon.run";

function req(overrides: Partial<{ method: string; url: string; mode: string; origin: string }> = {}) {
  return {
    method: "GET",
    url: `${ORIGIN}/dashboard`,
    mode: "cors",
    origin: ORIGIN,
    ...overrides,
  };
}

describe("classifyRequest", () => {
  it("ignores every non-GET request regardless of path", () => {
    expect(D.classifyRequest(req({ method: "POST", url: `${ORIGIN}/api/orders/place` }))).toBe("ignore");
    expect(D.classifyRequest(req({ method: "POST", url: `${ORIGIN}/api/previous-close` }))).toBe("ignore");
    expect(D.classifyRequest(req({ method: "PUT", url: `${ORIGIN}/dashboard` }))).toBe("ignore");
  });

  it("ignores cross-origin requests (Clerk, media host)", () => {
    expect(D.classifyRequest(req({ url: "https://clerk.radon.run/clerk.browser.js" }))).toBe("ignore");
    expect(D.classifyRequest(req({ url: "https://media.radon.run/x.png" }))).toBe("ignore");
  });

  it("bypasses /ws, /health and /_next/data (never respondWith)", () => {
    expect(D.classifyRequest(req({ url: `${ORIGIN}/ws` }))).toBe("bypass");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/health` }))).toBe("bypass");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/_next/data/x` }))).toBe("bypass");
  });

  it("bypasses non-allowlisted /api paths", () => {
    expect(D.classifyRequest(req({ url: `${ORIGIN}/api/prices` }))).toBe("bypass");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/api/orders/place` }))).toBe("bypass");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/api/admin/health` }))).toBe("bypass");
  });

  it("classifies allowlisted /api GETs as api, including per-ticker query routes", () => {
    expect(D.classifyRequest(req({ url: `${ORIGIN}/api/portfolio` }))).toBe("api");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/api/orders` }))).toBe("api");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/api/ticker/info?ticker=NVDA` }))).toBe("api");
  });

  it("classifies hard navigations as navigation", () => {
    expect(D.classifyRequest(req({ mode: "navigate", url: `${ORIGIN}/dashboard` }))).toBe("navigation");
    expect(D.classifyRequest(req({ mode: "navigate", url: `${ORIGIN}/orders` }))).toBe("navigation");
  });

  it("ignores RSC soft-nav fetches (page path with mode cors)", () => {
    expect(D.classifyRequest(req({ mode: "cors", url: `${ORIGIN}/dashboard?_rsc=abc` }))).toBe("ignore");
    expect(D.classifyRequest(req({ mode: "cors", url: `${ORIGIN}/dashboard` }))).toBe("ignore");
  });

  it("classifies static assets as static", () => {
    expect(D.classifyRequest(req({ url: `${ORIGIN}/_next/static/chunk.js` }))).toBe("static");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/manifest.webmanifest` }))).toBe("static");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/icons/icon-192.png` }))).toBe("static");
    expect(D.classifyRequest(req({ url: `${ORIGIN}/images/x.png` }))).toBe("static");
  });
});

describe("isCacheworthyApiBody", () => {
  it("accepts non-empty JSON payloads", () => {
    expect(D.isCacheworthyApiBody('{"positions":[]}')).toBe(true);
    expect(D.isCacheworthyApiBody('{"a":1}')).toBe(true);
    expect(D.isCacheworthyApiBody("[1,2]")).toBe(true);
  });

  it("rejects empty/null/non-JSON bodies (never cache empty results)", () => {
    expect(D.isCacheworthyApiBody("")).toBe(false);
    expect(D.isCacheworthyApiBody("{}")).toBe(false);
    expect(D.isCacheworthyApiBody("[]")).toBe(false);
    expect(D.isCacheworthyApiBody("null")).toBe(false);
    expect(D.isCacheworthyApiBody("<html>error</html>")).toBe(false);
  });
});

describe("shouldCacheResponse", () => {
  it("caches only 200 basic/default responses", () => {
    expect(D.shouldCacheResponse({ ok: true, status: 200, type: "basic" })).toBe(true);
    expect(D.shouldCacheResponse({ ok: true, status: 200, type: "default" })).toBe(true);
  });

  it("never caches 304/401/403/500/redirect responses", () => {
    expect(D.shouldCacheResponse({ ok: false, status: 304, type: "basic" })).toBe(false);
    expect(D.shouldCacheResponse({ ok: false, status: 401, type: "basic" })).toBe(false);
    expect(D.shouldCacheResponse({ ok: false, status: 403, type: "basic" })).toBe(false);
    expect(D.shouldCacheResponse({ ok: false, status: 500, type: "basic" })).toBe(false);
    expect(D.shouldCacheResponse({ ok: false, status: 302, type: "basic" })).toBe(false);
    expect(D.shouldCacheResponse({ ok: true, status: 200, type: "opaqueredirect" })).toBe(false);
  });
});

describe("navigationCacheKey", () => {
  it("keys navigations by pathname only (drops search + hash)", () => {
    expect(D.navigationCacheKey("https://app.radon.run/dashboard?x=1#y")).toBe("/dashboard");
    expect(D.navigationCacheKey("https://app.radon.run/orders")).toBe("/orders");
  });
});

describe("selectEvictions", () => {
  const entries = [
    { url: `${ORIGIN}/a`, cachedAt: "2026-01-03T00:00:00.000Z" },
    { url: `${ORIGIN}/b`, cachedAt: "2026-01-01T00:00:00.000Z" },
    { url: `${ORIGIN}/c`, cachedAt: "2026-01-02T00:00:00.000Z" },
  ];

  it("returns the oldest surplus urls when over limit", () => {
    expect(D.selectEvictions(entries, 1)).toEqual([`${ORIGIN}/b`, `${ORIGIN}/c`]);
    expect(D.selectEvictions(entries, 2)).toEqual([`${ORIGIN}/b`]);
  });

  it("returns [] at or under the limit", () => {
    expect(D.selectEvictions(entries, 3)).toEqual([]);
    expect(D.selectEvictions(entries, 5)).toEqual([]);
    expect(D.selectEvictions([], 1)).toEqual([]);
  });
});

describe("offline stamps", () => {
  it("buildCachedAtStamp stamps the injected clock as ISO", () => {
    const nowMs = Date.UTC(2026, 0, 2, 3, 4, 5);
    expect(D.buildCachedAtStamp(nowMs)).toEqual({ "X-Radon-Cached-At": "2026-01-02T03:04:05.000Z" });
  });

  it("buildOfflineServeHeaders adds the offline marker and preserves the cached-at stamp", () => {
    const out = D.buildOfflineServeHeaders({
      "content-type": "application/json",
      "X-Radon-Cached-At": "2026-01-02T03:04:05.000Z",
    });
    expect(out["X-Radon-Offline"]).toBe("1");
    expect(out["X-Radon-Cached-At"]).toBe("2026-01-02T03:04:05.000Z");
    expect(out["content-type"]).toBe("application/json");
  });
});

describe("constant pins", () => {
  it("KNOWN_CACHES is exactly the three radon caches, pages/api versioned by SW_VERSION", () => {
    expect(D.KNOWN_CACHES).toEqual([D.STATIC_CACHE, D.PAGES_CACHE, D.API_CACHE]);
    expect(D.STATIC_CACHE).toBe("radon-static-v1");
    // Schema version folded into the NAMES: bumping SW_VERSION makes
    // activate drop old-format pages/api entries. Static stays unversioned.
    expect(D.PAGES_CACHE).toBe(`radon-pages-${D.SW_VERSION}`);
    expect(D.API_CACHE).toBe(`radon-api-${D.SW_VERSION}`);
  });

  it("pins the documented cache bounds", () => {
    expect(D.MAX_PAGE_ENTRIES).toBe(30);
    expect(D.MAX_API_ENTRIES).toBe(100);
  });

  it("precaches the existing shell assets plus /offline.html", () => {
    expect(D.PRECACHE_URLS).toEqual([
      "/manifest.webmanifest",
      "/icons/icon-192.png",
      "/icons/icon-512.png",
      "/icons/apple-touch-icon.png",
      "/offline.html",
    ]);
  });

  it("OFFLINE_API_PATHS covers the allowlist and nothing dangerous", () => {
    const expected = [
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
    for (const path of expected) {
      expect(D.OFFLINE_API_PATHS, `${path} allowlisted`).toContain(path);
    }
    expect(D.OFFLINE_API_PATHS).not.toContain("/api/orders/place");
    expect(D.OFFLINE_API_PATHS).not.toContain("/api/prices");
    expect(D.OFFLINE_API_PATHS).not.toContain("/ws");
    expect(D.OFFLINE_API_PATHS).not.toContain("/health");
  });

  it("exports a schema version", () => {
    expect(typeof D.SW_VERSION).toBe("string");
    expect(D.SW_VERSION.length).toBeGreaterThan(0);
  });
});
