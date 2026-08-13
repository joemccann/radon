import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve, dirname } from "node:path";
import vm from "node:vm";

const PUBLIC_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../public");
const ORIGIN = "https://app.radon.run";
type Listener = (event: any) => void;

function loadSw() {
  const decisionsSrc = readFileSync(resolve(PUBLIC_DIR, "sw-decisions.js"), "utf8");
  const swSrc = readFileSync(resolve(PUBLIC_DIR, "sw.js"), "utf8");
  const listeners: Record<string, Listener[]> = {};
  const stores = new Map<string, Map<string, Response>>();
  const cache = (name: string) => {
    if (!stores.has(name)) stores.set(name, new Map());
    const store = stores.get(name)!;
    return {
      match: vi.fn(async (request: { url: string }) => store.get(request.url)?.clone()),
      put: vi.fn(async (request: { url: string }, response: Response) => { store.set(request.url, response); }),
      keys: vi.fn(async () => [...store.keys()].map((url) => ({ url }))),
      delete: vi.fn(async (url: string) => store.delete(url)),
      addAll: vi.fn(async () => {}),
    };
  };
  const caches = {
    open: vi.fn(async (name: string) => cache(name)),
    keys: vi.fn(async () => [...stores.keys()]),
    delete: vi.fn(async (name: string) => stores.delete(name)),
  };
  const fetchMock = vi.fn();
  const self = {
    addEventListener: (name: string, fn: Listener) => { (listeners[name] ||= []).push(fn); },
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn() },
    location: { origin: ORIGIN },
  } as Record<string, any>;
  const sandbox: Record<string, any> = {
    self, caches, fetch: fetchMock, URL, Response, console,
    importScripts: () => vm.runInNewContext(decisionsSrc, sandbox),
  };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(swSrc, sandbox);
  return { listeners, self, caches, stores, fetchMock };
}

function dispatchFetch(h: ReturnType<typeof loadSw>, request: { url: string; method?: string; mode?: string }) {
  const waits: Promise<unknown>[] = [];
  const event = {
    request: { method: "GET", mode: "cors", ...request },
    respondWith: vi.fn(),
    waitUntil: (promise: Promise<unknown>) => waits.push(promise),
  };
  for (const fn of h.listeners.fetch ?? []) fn(event);
  return { event, waits };
}

let h: ReturnType<typeof loadSw>;
beforeEach(() => { h = loadSw(); });

describe("service worker authenticated-data isolation", () => {
  it("never intercepts navigations, APIs, RSC data, or sockets", () => {
    const requests = [
      { url: `${ORIGIN}/portfolio`, mode: "navigate" },
      { url: `${ORIGIN}/api/portfolio` },
      { url: `${ORIGIN}/_next/data/build/portfolio.json` },
      { url: `${ORIGIN}/ws` },
    ];
    for (const request of requests) {
      expect(dispatchFetch(h, request).event.respondWith).not.toHaveBeenCalled();
    }
    expect(h.fetchMock).not.toHaveBeenCalled();
  });

  it("intercepts and caches a clean public static asset", async () => {
    const network = new Response("chunk", { status: 200 });
    h.fetchMock.mockResolvedValue(network);
    const dispatched = dispatchFetch(h, { url: `${ORIGIN}/_next/static/chunk.js` });
    expect(dispatched.event.respondWith).toHaveBeenCalledTimes(1);
    expect(await dispatched.event.respondWith.mock.calls[0][0]).toBe(network);
    await Promise.allSettled(dispatched.waits);
  });

  it("activation deletes every legacy page/API cache", async () => {
    h.stores.set("radon-pages-old", new Map());
    h.stores.set("radon-api-old", new Map());
    const waits: Promise<unknown>[] = [];
    for (const fn of h.listeners.activate ?? []) fn({ waitUntil: (promise: Promise<unknown>) => waits.push(promise) });
    await Promise.all(waits);
    expect(h.caches.delete).toHaveBeenCalledWith("radon-pages-old");
    expect(h.caches.delete).toHaveBeenCalledWith("radon-api-old");
  });

  it("purge messages delete legacy authenticated caches and preserve static", async () => {
    h.stores.set("radon-pages-old", new Map());
    h.stores.set("radon-api-old", new Map());
    h.stores.set(h.self.RadonSwDecisions.STATIC_CACHE, new Map());
    const waits: Promise<unknown>[] = [];
    for (const fn of h.listeners.message ?? []) {
      fn({ data: { type: "radon-clear-caches" }, waitUntil: (promise: Promise<unknown>) => waits.push(promise) });
    }
    await Promise.all(waits);
    expect(h.caches.delete).toHaveBeenCalledWith("radon-pages-old");
    expect(h.caches.delete).toHaveBeenCalledWith("radon-api-old");
    expect(h.caches.delete).not.toHaveBeenCalledWith(h.self.RadonSwDecisions.STATIC_CACHE);
  });
});
