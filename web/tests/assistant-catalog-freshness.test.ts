import { describe, expect, it } from "vitest";

import {
  authorize,
  catalogOperations,
  search,
} from "@/lib/assistant/catalog";
import {
  advertisedPins,
  buildRuntimeCatalog,
  findFastApiTwin,
  isAdvertisedPin,
} from "@/lib/assistant/catalogBuild";
import {
  loadPinSourcesFromDisk,
  parseFastApiCatalog,
  parseNextRoute,
  type PinSources,
} from "@/lib/assistant/pinSources";
import { NEXT_MODULES } from "@/lib/assistant/nextLoaders";

/**
 * Adversarial freshness: list_apis / call_api must track pin sources, not a
 * handwritten OPERATIONS seed. REL-161 parity only checked OPERATIONS ⊆ pins,
 * so GET /streaks/{ticker} shipped classified and still invisible to chat.
 */

function keys(ops: { method: string; path: string }[]): string[] {
  return ops.map((item) => `${item.method} ${item.path}`).sort();
}

function fixtureSources(extraFastApi = "", extraNext: Array<{ routeId: string; source: string }> = []): PinSources {
  const disk = loadPinSourcesFromDisk();
  const fastapi = parseFastApiCatalog(
    `${disk.fastapi.map((pin) => `("${pin.method}", "${pin.path}"): "${pin.capability}"`).join("\n")}\n${extraFastApi}`,
  );
  const next = [
    ...disk.next,
    ...extraNext.flatMap((item) => parseNextRoute(item.routeId, item.source)),
  ];
  return { fastapi, next, root: disk.root };
}

describe("assistant catalog freshness", () => {
  const sources = loadPinSourcesFromDisk();
  const ops = catalogOperations();

  it("resolves pin files from disk (cwd and import.meta.url both work)", () => {
    expect(sources.fastapi.length).toBeGreaterThan(40);
    expect(sources.next.length).toBeGreaterThan(40);
    expect(sources.fastapi.some((pin) => pin.path === "/streaks/{ticker}" && pin.method === "GET")).toBe(
      true,
    );
  });

  it("list_apis('streaks') returns GET /streaks/{ticker}", () => {
    const hits = search("streaks");
    expect(hits.map((hit) => `${hit.method} ${hit.path}`)).toContain("GET /streaks/{ticker}");
  });

  it("list_apis('win streak') still finds /streaks/{ticker} (AND-all extra words must not hide it)", () => {
    const hits = search("win streak");
    expect(hits.map((hit) => `${hit.method} ${hit.path}`)).toContain("GET /streaks/{ticker}");
  });

  it("authorize GET /streaks/NVDA is a FastAPI read", () => {
    const result = authorize("GET", "/streaks/NVDA");
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.surface).toBe("fastapi");
    expect(result.capability).toBe("read");
    expect(result.operation.path).toBe("/streaks/{ticker}");
  });

  it("Next /api/streaks is a FastAPI twin, so chat is not sent to an unwired Next proxy", () => {
    const twin = findFastApiTwin("/api/streaks", "GET", sources.fastapi);
    expect(twin?.path).toBe("/streaks/{ticker}");
    expect(ops.some((item) => item.surface === "next" && item.path === "/api/streaks")).toBe(false);
  });

  it("every advertised FastAPI pin is in the runtime catalog (reverse of REL-161)", () => {
    const missing = sources.fastapi
      .filter((pin) => isAdvertisedPin(pin))
      .map((pin) => `${pin.method} ${pin.path}`)
      .filter((key) => !ops.some((item) => `${item.method} ${item.path}` === key));
    expect(missing).toEqual([]);
  });

  it("every advertised Next-only pin is in the runtime catalog", () => {
    const missing = advertisedPins(sources)
      .filter((pin) => pin.surface === "next")
      .map((pin) => `${pin.method} ${pin.path}`)
      .filter((key) => !ops.some((item) => `${item.method} ${item.path}` === key));
    expect(missing).toEqual([]);
  });

  it("runtime catalog is exactly buildRuntimeCatalog(disk pins)", () => {
    expect(keys(ops)).toEqual(keys(buildRuntimeCatalog(sources)));
  });

  it("knowledge and admin pins are not advertised", () => {
    const advertised = new Set(keys(ops));
    expect(advertised.has("GET /knowledge/prior-evals")).toBe(false);
    expect(advertised.has("POST /knowledge/search")).toBe(false);
    expect(advertised.has("POST /orders/place")).toBe(false);
    expect(advertised.has("GET /admin/services")).toBe(false);
    expect(search("knowledge").some((hit) => hit.path.includes("knowledge"))).toBe(false);
  });

  it("SSRF .. on a streaks path cannot reach orders/place", () => {
    const result = authorize("GET", "/streaks/../orders/place");
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.capability).toBe("mutate.trading");
  });

  it("adding a FastAPI GET pin appears; removing it disappears", () => {
    const withNew = fixtureSources('("GET", "/new-indicator/{ticker}"): "read"');
    expect(keys(buildRuntimeCatalog(withNew))).toContain("GET /new-indicator/{ticker}");

    const withoutStreaks: PinSources = {
      ...sources,
      fastapi: sources.fastapi.filter((pin) => pin.path !== "/streaks/{ticker}"),
    };
    expect(keys(buildRuntimeCatalog(withoutStreaks))).not.toContain("GET /streaks/{ticker}");
  });

  it("adding a Next-only GET read pin appears (new-indicator path)", () => {
    const withNew = fixtureSources("", [
      {
        routeId: "brand-new-tab",
        source: 'export const radonCapability = "read";\nexport async function GET() { return new Response("ok"); }\n',
      },
    ]);
    expect(keys(buildRuntimeCatalog(withNew))).toContain("GET /api/brand-new-tab");
  });

  it("every Next-only advertised route has a static loader (bundler-safe dispatch)", () => {
    const needed = advertisedPins(sources)
      .filter((pin) => pin.surface === "next" && pin.routeId)
      .map((pin) => pin.routeId as string)
      .sort();
    expect([...new Set(needed)].sort()).toEqual(Object.keys(NEXT_MODULES).sort());
  });

  it("watchlist stays a Next surface", () => {
    const watch = ops.filter((item) => item.path.startsWith("/api/watchlist"));
    expect(watch.map((item) => `${item.method} ${item.path}`).sort()).toEqual([
      "DELETE /api/watchlist/{symbol}",
      "GET /api/watchlist",
      "POST /api/watchlist",
    ]);
    expect(watch.every((item) => item.surface === "next")).toBe(true);
  });

  it("existing operator-only FastAPI mutations stay gated", () => {
    const gated = ops
      .filter((item) => item.operatorOnly)
      .map((item) => `${item.method} ${item.path}`);
    expect(gated).toEqual(expect.arrayContaining([
      "POST /orders/refresh",
      "POST /performance",
      "POST /portfolio/sync",
    ]));
    expect(ops.find((item) => item.path === "/quote/{ticker}")?.operatorOnly).toBeFalsy();
  });
});
