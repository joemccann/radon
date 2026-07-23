/**
 * Behavioral smoke test of web/public/sw.js — evaluates the real SW source in
 * a vm sandbox with a mocked `self`/`caches`/`fetch` and dispatches synthetic
 * fetch events. This is the runtime pin of the cache contract's offline
 * exception: the SW is network-first everywhere it intercepts, and Cache
 * Storage is read ONLY when fetch() rejects (offline). Online responses are
 * returned untouched (asserted with toBe on the exact Response object).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve, dirname } from "node:path";
import vm from "node:vm";

const PUBLIC_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../public");
const ORIGIN = "https://app.radon.run";

type Listener = (event: any) => void;

function absUrl(key: unknown): string {
  const raw = typeof key === "string" ? key : (key as { url: string }).url;
  return raw.startsWith("/") ? `${ORIGIN}${raw}` : raw;
}

function createMockCache() {
  const store = new Map<string, Response>();
  return {
    store,
    match: vi.fn(async (key: unknown) => {
      const hit = store.get(absUrl(key));
      return hit ? hit.clone() : undefined;
    }),
    put: vi.fn(async (key: unknown, res: Response) => {
      store.set(absUrl(key), res);
    }),
    delete: vi.fn(async (key: unknown) => store.delete(absUrl(key))),
    keys: vi.fn(async () => [...store.keys()].map((url) => ({ url }))),
    addAll: vi.fn(async (urls: string[]) => {
      for (const url of urls) store.set(absUrl(url), new Response(`precached:${url}`));
    }),
  };
}

type Harness = ReturnType<typeof loadSw>;

function loadSw() {
  const decisionsSrc = readFileSync(resolve(PUBLIC_DIR, "sw-decisions.js"), "utf8");
  const swSrc = readFileSync(resolve(PUBLIC_DIR, "sw.js"), "utf8");

  const listeners: Record<string, Listener[]> = {};
  const cacheStores = new Map<string, ReturnType<typeof createMockCache>>();
  const openStore = (name: string) => {
    if (!cacheStores.has(name)) cacheStores.set(name, createMockCache());
    return cacheStores.get(name)!;
  };

  const caches = {
    open: vi.fn(async (name: string) => openStore(name)),
    delete: vi.fn(async (name: string) => cacheStores.delete(name)),
    keys: vi.fn(async () => [...cacheStores.keys()]),
    match: vi.fn(async (key: unknown) => {
      for (const store of cacheStores.values()) {
        const hit = store.store.get(absUrl(key));
        if (hit) return hit.clone();
      }
      return undefined;
    }),
  };

  const fetchMock = vi.fn();
  const self = {
    addEventListener: (name: string, fn: Listener) => {
      (listeners[name] ||= []).push(fn);
    },
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn() },
    location: { origin: ORIGIN },
  } as Record<string, any>;

  const sandbox: Record<string, any> = {
    self,
    caches,
    fetch: fetchMock,
    URL,
    Response,
    Headers,
    console,
    importScripts: (path: string) => {
      if (path !== "/sw-decisions.js") throw new Error(`unexpected importScripts: ${path}`);
      vm.runInNewContext(decisionsSrc, sandbox);
    },
  };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(swSrc, sandbox);

  return { listeners, self, caches, cacheStores, openStore, fetchMock };
}

function dispatchFetch(h: Harness, request: { url: string; method?: string; mode?: string }) {
  const waitUntils: Promise<unknown>[] = [];
  const event = {
    request: { method: "GET", mode: "cors", ...request },
    respondWith: vi.fn(),
    waitUntil: vi.fn((p: Promise<unknown>) => {
      waitUntils.push(p);
    }),
  };
  for (const fn of h.listeners.fetch ?? []) fn(event);
  return {
    event,
    responded: event.respondWith.mock.calls.length > 0,
    response: (): Promise<Response> => event.respondWith.mock.calls[0][0],
    settle: async () => {
      await Promise.allSettled(waitUntils);
    },
  };
}

async function seedCachedPage(h: Harness, cacheName: string, key: string, body: string, headers: Record<string, string>) {
  const store = h.openStore(cacheName);
  store.store.set(absUrl(key), new Response(body, { status: 200, headers }));
}

let h: Harness;
beforeEach(() => {
  h = loadSw();
});

describe("interception surface", () => {
  it("never calls respondWith for POST, /ws, /health, non-allowlisted /api, cross-origin", () => {
    const untouched = [
      { url: `${ORIGIN}/api/orders/place`, method: "POST" },
      { url: `${ORIGIN}/ws` },
      { url: `${ORIGIN}/health` },
      { url: `${ORIGIN}/api/prices` },
      { url: `${ORIGIN}/_next/data/x` },
      { url: "https://clerk.radon.run/clerk.browser.js" },
    ];
    for (const request of untouched) {
      const d = dispatchFetch(h, request);
      expect(d.responded, `${request.method ?? "GET"} ${request.url} must pass through`).toBe(false);
    }
  });

  it("calls respondWith for navigations, allowlisted API GETs, static assets", async () => {
    // Settle every dispatched handler: an unconfigured fetch mock resolves
    // undefined and the un-awaited handler promise becomes an unhandled
    // rejection that fails CI even with all tests green (2026-07-22).
    h.fetchMock.mockResolvedValue(
      new Response("{}", { status: 200, headers: { "content-type": "application/json" } }),
    );
    const dispatches = [
      dispatchFetch(h, { url: `${ORIGIN}/dashboard`, mode: "navigate" }),
      dispatchFetch(h, { url: `${ORIGIN}/api/portfolio` }),
      dispatchFetch(h, { url: `${ORIGIN}/_next/static/x.js` }),
    ];
    for (const d of dispatches) {
      expect(d.responded).toBe(true);
      await d.response();
      await d.settle();
    }
  });
});

describe("navigation branch", () => {
  it("online: returns the exact network Response, never reads cache, writes a stamped copy", async () => {
    const network = new Response("<html>dash</html>", {
      status: 200,
      headers: { "content-type": "text/html", "content-security-policy": "script-src 'nonce-abc'" },
    });
    h.fetchMock.mockResolvedValue(network);

    const d = dispatchFetch(h, { url: `${ORIGIN}/dashboard?x=1`, mode: "navigate" });
    const res = await d.response();
    expect(res).toBe(network);
    await d.settle();

    const pages = h.openStore(h.self.RadonSwDecisions.PAGES_CACHE);
    expect(pages.match).not.toHaveBeenCalled();
    expect(pages.put).toHaveBeenCalledTimes(1);
    const [key, stored] = pages.put.mock.calls[0];
    expect(key).toBe("/dashboard");
    expect((stored as Response).headers.get("X-Radon-Cached-At")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect((stored as Response).headers.get("content-security-policy")).toBe("script-src 'nonce-abc'");
  });

  it("online 500: returns it untouched, no cache write, no cache read", async () => {
    const network = new Response("boom", { status: 500 });
    h.fetchMock.mockResolvedValue(network);

    const d = dispatchFetch(h, { url: `${ORIGIN}/dashboard`, mode: "navigate" });
    expect(await d.response()).toBe(network);
    await d.settle();

    const pages = h.openStore(h.self.RadonSwDecisions.PAGES_CACHE);
    expect(pages.put).not.toHaveBeenCalled();
    expect(pages.match).not.toHaveBeenCalled();
  });

  it("offline with a cached page: serves it with X-Radon-Offline + X-Radon-Cached-At", async () => {
    await seedCachedPage(h, h.self.RadonSwDecisions.PAGES_CACHE, "/dashboard", "<html>cached</html>", {
      "content-type": "text/html",
      "X-Radon-Cached-At": "2026-01-02T03:04:05.000Z",
    });
    h.fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const d = dispatchFetch(h, { url: `${ORIGIN}/dashboard`, mode: "navigate" });
    const res = await d.response();
    expect(res.headers.get("X-Radon-Offline")).toBe("1");
    expect(res.headers.get("X-Radon-Cached-At")).toBe("2026-01-02T03:04:05.000Z");
    expect(await res.text()).toBe("<html>cached</html>");
  });

  it("offline with no cached page: serves the precached /offline.html", async () => {
    await seedCachedPage(h, "radon-static-v1", "/offline.html", "offline shell", {
      "content-type": "text/html",
    });
    h.fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const d = dispatchFetch(h, { url: `${ORIGIN}/never-visited`, mode: "navigate" });
    const res = await d.response();
    expect(await res.text()).toBe("offline shell");
  });
});

describe("api branch", () => {
  it("online: returns the exact network Response, never reads cache, stamps a copy keyed by full URL", async () => {
    const network = new Response('{"positions":[1]}', {
      status: 200,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
    h.fetchMock.mockResolvedValue(network);

    const d = dispatchFetch(h, { url: `${ORIGIN}/api/portfolio` });
    expect(await d.response()).toBe(network);
    await d.settle();

    const api = h.openStore(h.self.RadonSwDecisions.API_CACHE);
    expect(api.match).not.toHaveBeenCalled();
    expect(api.put).toHaveBeenCalledTimes(1);
    const [key, stored] = api.put.mock.calls[0];
    expect(key).toBe(`${ORIGIN}/api/portfolio`);
    expect((stored as Response).headers.get("X-Radon-Cached-At")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(await (stored as Response).text()).toBe('{"positions":[1]}');
  });

  it("online degraded 200 (route validator rejects): passes through, never overwrites cache", async () => {
    // /api/ticker/info serving uw_info: {} is the 200-degraded shape — caching
    // it would overwrite last-known-good and offline would replay the
    // degraded body (see API_BODY_VALIDATORS in sw-decisions.js).
    const network = new Response('{"uw_info":{},"stock_state":{},"short_float":{}}', {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    h.fetchMock.mockResolvedValue(network);

    const d = dispatchFetch(h, { url: `${ORIGIN}/api/ticker/info?ticker=NVDA` });
    expect(await d.response()).toBe(network);
    await d.settle();

    expect(h.openStore(h.self.RadonSwDecisions.API_CACHE).put).not.toHaveBeenCalled();
  });

  it("online empty payload: passes through but never caches it", async () => {
    const network = new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
    h.fetchMock.mockResolvedValue(network);

    const d = dispatchFetch(h, { url: `${ORIGIN}/api/portfolio` });
    expect(await d.response()).toBe(network);
    await d.settle();
    expect(h.openStore(h.self.RadonSwDecisions.API_CACHE).put).not.toHaveBeenCalled();
  });

  it("online 500: returns it untouched, no cache write, no cache read", async () => {
    const network = new Response("err", { status: 500 });
    h.fetchMock.mockResolvedValue(network);

    const d = dispatchFetch(h, { url: `${ORIGIN}/api/portfolio` });
    expect(await d.response()).toBe(network);
    await d.settle();
    const api = h.openStore(h.self.RadonSwDecisions.API_CACHE);
    expect(api.put).not.toHaveBeenCalled();
    expect(api.match).not.toHaveBeenCalled();
  });

  it("offline with a cached payload: serves it with offline headers", async () => {
    await seedCachedPage(h, h.self.RadonSwDecisions.API_CACHE, `${ORIGIN}/api/portfolio`, '{"positions":[1]}', {
      "content-type": "application/json",
      "X-Radon-Cached-At": "2026-01-02T03:04:05.000Z",
    });
    h.fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const d = dispatchFetch(h, { url: `${ORIGIN}/api/portfolio` });
    const res = await d.response();
    expect(res.headers.get("X-Radon-Offline")).toBe("1");
    expect(res.headers.get("X-Radon-Cached-At")).toBe("2026-01-02T03:04:05.000Z");
    expect(await res.text()).toBe('{"positions":[1]}');
  });

  it("offline with no cached payload: rejects (hooks keep today's failure semantics)", async () => {
    h.fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const d = dispatchFetch(h, { url: `${ORIGIN}/api/portfolio` });
    await expect(d.response()).rejects.toThrow("Failed to fetch");
  });
});

describe("static branch", () => {
  it("stays cache-first: a cached asset wins outright without a network fetch", async () => {
    await seedCachedPage(h, "radon-static-v1", `${ORIGIN}/_next/static/x.js`, "chunk", {
      "content-type": "application/javascript",
    });

    const d = dispatchFetch(h, { url: `${ORIGIN}/_next/static/x.js` });
    const res = await d.response();
    expect(await res.text()).toBe("chunk");
    expect(h.fetchMock).not.toHaveBeenCalled();
  });
});

describe("lifecycle + messaging", () => {
  it("install precaches into radon-static-v1 (including /offline.html) and skipWaiting", async () => {
    const waits: Promise<unknown>[] = [];
    for (const fn of h.listeners.install ?? []) fn({ waitUntil: (p: Promise<unknown>) => waits.push(p) });
    await Promise.all(waits);

    const statics = h.openStore("radon-static-v1");
    expect(statics.addAll).toHaveBeenCalledTimes(1);
    expect(statics.addAll.mock.calls[0][0]).toContain("/offline.html");
    expect(h.self.skipWaiting).toHaveBeenCalled();
  });

  it("activate deletes unknown caches (old radon-shell-v1) and keeps all KNOWN_CACHES", async () => {
    h.openStore("radon-shell-v1");
    h.openStore("radon-static-v1");
    h.openStore(h.self.RadonSwDecisions.PAGES_CACHE);
    h.openStore(h.self.RadonSwDecisions.API_CACHE);

    const waits: Promise<unknown>[] = [];
    for (const fn of h.listeners.activate ?? []) fn({ waitUntil: (p: Promise<unknown>) => waits.push(p) });
    await Promise.all(waits);

    expect(h.caches.delete).toHaveBeenCalledWith("radon-shell-v1");
    expect(h.caches.delete).not.toHaveBeenCalledWith("radon-static-v1");
    expect(h.caches.delete).not.toHaveBeenCalledWith(h.self.RadonSwDecisions.PAGES_CACHE);
    expect(h.caches.delete).not.toHaveBeenCalledWith(h.self.RadonSwDecisions.API_CACHE);
    expect(h.self.clients.claim).toHaveBeenCalled();
  });

  it("radon-clear-caches message purges pages + api caches, keeps the static shell", async () => {
    const waits: Promise<unknown>[] = [];
    for (const fn of h.listeners.message ?? []) {
      fn({ data: { type: "radon-clear-caches" }, waitUntil: (p: Promise<unknown>) => waits.push(p) });
    }
    await Promise.all(waits);

    expect(h.caches.delete).toHaveBeenCalledWith(h.self.RadonSwDecisions.PAGES_CACHE);
    expect(h.caches.delete).toHaveBeenCalledWith(h.self.RadonSwDecisions.API_CACHE);
    expect(h.caches.delete).not.toHaveBeenCalledWith("radon-static-v1");
  });

  it("ignores unrelated messages", async () => {
    for (const fn of h.listeners.message ?? []) {
      fn({ data: { type: "something-else" }, waitUntil: vi.fn() });
    }
    expect(h.caches.delete).not.toHaveBeenCalled();
  });
});
