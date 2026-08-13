import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve, dirname } from "node:path";
import vm from "node:vm";

const src = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../public/sw-decisions.js"), "utf8");
const sandbox: Record<string, unknown> = { module: { exports: {} }, URL };
vm.runInNewContext(src, sandbox);
const D = (sandbox.module as { exports: Record<string, any> }).exports;
const ORIGIN = "https://app.radon.run";
const classify = (path: string, mode = "cors") => D.classifyRequest({
  method: "GET",
  url: `${ORIGIN}${path}`,
  mode,
  origin: ORIGIN,
});

describe("service worker isolation decisions", () => {
  it("bypasses every authenticated page, API, data, and socket request", () => {
    expect(classify("/portfolio", "navigate")).toBe("bypass");
    expect(classify("/api/portfolio")).toBe("bypass");
    expect(classify("/_next/data/build/portfolio.json")).toBe("bypass");
    expect(classify("/ws")).toBe("bypass");
  });

  it("intercepts only same-origin static public assets", () => {
    expect(classify("/_next/static/chunk.js")).toBe("static");
    expect(classify("/icons/icon-192.png")).toBe("static");
    expect(classify("/images/hero.png")).toBe("static");
    expect(classify("/manifest.webmanifest")).toBe("static");
    expect(D.classifyRequest({ method: "GET", url: "https://clerk.example/x.js", mode: "cors", origin: ORIGIN })).toBe("ignore");
  });

  it("has no page or API cache in the known cache set or precache", () => {
    expect(D.KNOWN_CACHES).toEqual([D.STATIC_CACHE]);
    expect(D.KNOWN_CACHES.join(" ")).not.toMatch(/radon-(pages|api)-/);
    expect(D.PRECACHE_URLS).not.toContain("/offline.html");
  });

  it("caches only clean public asset responses", () => {
    expect(D.shouldCacheResponse({ ok: true, status: 200, type: "basic" })).toBe(true);
    expect(D.shouldCacheResponse({ ok: false, status: 401, type: "basic" })).toBe(false);
    expect(D.shouldCacheResponse({ ok: true, status: 200, type: "opaque" })).toBe(false);
  });

  it("never evicts precached static assets when enforcing the bound", () => {
    const urls = [
      `${ORIGIN}/manifest.webmanifest`,
      `${ORIGIN}/_next/static/old.js`,
      `${ORIGIN}/_next/static/new.js`,
    ];
    expect(D.selectStaticEvictions(urls, 2)).toEqual([`${ORIGIN}/_next/static/old.js`]);
  });
});
