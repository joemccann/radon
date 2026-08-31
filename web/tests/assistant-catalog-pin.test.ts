import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CAPABILITIES,
  isRefused,
  type Capability,
  type RouteCapability,
} from "@/lib/assistant/capabilities";

const CAPABILITY_SET = new Set<string>(CAPABILITIES);

type PinnedCapability = Capability | Record<string, Capability>;

/**
 * Exhaustive chat-capability pin for every Next.js API route file.
 * A disk route missing from this map is unclassified. A listed path
 * missing on disk is stale. Values must match `export const radonCapability`.
 */
const PINNED: Record<string, PinnedCapability> = {
  "admin/demo-users": { GET: "admin", POST: "admin" },
  "admin/edge-health": "admin",
  "admin/health": "admin",
  "admin/host-metrics": "admin",
  "admin/ib/reset-backoff": "admin",
  "admin/ib/restart": "admin",
  "admin/reliability": "admin",
  "admin/services": "admin",
  "admin/services/[unit]/[action]": "admin",
  "admin/slo": "admin",
  "admin/stack/restart": "admin",
  "admin/trading/[action]": { GET: "admin", POST: "admin" },
  "alerts": { GET: "read", POST: "mutate.workspace" },
  "alerts/[id]": "mutate.workspace",
  "assistant": "internal",
  "attribution": "read",
  "backtest/[strategy]": "read",
  "blotter": { GET: "read", POST: "mutate.workspace" },
  "bookmarks": { GET: "read", POST: "mutate.workspace" },
  "bookmarks/[post_id]": "mutate.workspace",
  "bpi": "read",
  "breadth": { GET: "read", POST: "read.spawn" },
  "cash-flows": "read",
  "catalysts": "read",
  "cor": "read",
  "credit-spread": "read",
  "discover": { GET: "read", POST: "read.spawn" },
  "dispersion": "read",
  "divyield": "read",
  "equibles-ats-venue-share": "read",
  "equibles-cot-positioning": "read",
  "equibles-filing-forensics": "read",
  "equibles-short-crowding": "read",
  "equibles-smart-money-13f": "read",
  "flex-token": "read",
  "flow-analysis": { GET: "read", POST: "read.spawn" },
  "flow-analysis/[ticker]": { GET: "read", POST: "read.spawn" },
  "flow-surprise": "read",
  "futures-quote": "read",
  "futures/chain": "read",
  "gamma-rotation": { GET: "read", POST: "read.spawn" },
  "garch-convergence": "read",
  "garch-convergence/scan": "read.spawn",
  "gex": { GET: "read", POST: "read.spawn" },
  "gex/share": "internal",
  "gex/share/content": "internal",
  "hhlev": "read",
  "hyad": "read",
  "ib/ws-ticket": "admin",
  "iei-hyg": "read",
  "index-options/chain": "read",
  "index-quote": "read",
  "informed-flow/[ticker]": "read",
  "internals": { GET: "read", POST: "read.spawn" },
  "internals/share": "internal",
  "internals/share/content": "internal",
  "ivrank": "read",
  "journal": { GET: "read", POST: "mutate.workspace" },
  "journal/sync": "mutate.workspace",
  "knowledge/prior-evals": "read",
  "knowledge/search": "read",
  "leap": "read",
  "leap/scan": "read.spawn",
  "llm-token-index": "read",
  "margin-debt": "read",
  "menthorq/[command]/image": "read",
  "menthorq/cta": "read",
  "menthorq/cta/image": "read",
  "menthorq/cta/share": "internal",
  "menthorq/cta/share/content": "internal",
  "models": "read",
  "newsfeed/posts": "read",
  "options/chain": "read",
  "options/expirations": "read",
  "options/exposure": "read",
  "options/rv-ratio": { GET: "read", POST: "read.spawn" },
  "orders": { GET: "read", POST: "mutate.workspace" },
  "orders/cancel": "mutate.trading",
  "orders/modify": "mutate.trading",
  "orders/place": "mutate.trading",
  "orders/whatif": "read.spawn",
  "paper/place": "mutate.trading",
  "performance": { GET: "read", POST: "read.spawn" },
  "pi": "internal",
  "portfolio": { GET: "read", POST: "mutate.workspace" },
  "preferences": { GET: "read", PUT: "mutate.workspace", DELETE: "mutate.workspace" },
  "previous-close": "read",
  "prices": { GET: "read", POST: "read" },
  "probe/freshness": "internal",
  "profile": { GET: "read", PUT: "mutate.workspace" },
  "regime": { GET: "read", POST: "read.spawn" },
  "regime/share": "internal",
  "regime/share/content": "internal",
  "risk-free-rate": "read",
  "scanner": { GET: "read", POST: "read.spawn" },
  "scanner/strength": "read",
  "scanner/strength/scan": "read.spawn",
  "scanner/theta": "read",
  "scanner/theta/scan": "read.spawn",
  "service-health": "read",
  "share/pnl": "internal",
  "short-availability/[ticker]": "read",
  "skew": "read",
  "skew2d": "read",
  "straddle": "read",
  "streaks": "read",
  "ticker/info": "read",
  "ticker/news": "read",
  "ticker/ratings": "read",
  "ticker/seasonality": "read",
  "trin": "read",
  "vcg": "read",
  "vcg/share": "internal",
  "vcg/share/content": "internal",
  "vixcor": "read",
  "vixts": "read",
  "vol-cone": "read",
  "watchlist": { GET: "read", POST: "mutate.workspace" },
  "watchlist/[symbol]": "mutate.workspace",
  "webhooks/clerk": "internal",
  "workflow": { GET: "read", POST: "mutate.workspace" },
  "workflow/run": "mutate.trading",
  "yield-curve": "read",
  "yield-curve/live": "read",
};

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));

function collectApiRoutesFromFilesystem(): string[] {
  const apiRoot = resolve(WEB_ROOT, "app", "api");
  const found: string[] = [];

  function walk(dir: string, routePath: string): void {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        walk(join(dir, entry.name), routePath ? `${routePath}/${entry.name}` : entry.name);
      } else if (/^route\.(ts|tsx)$/.test(entry.name)) {
        found.push(routePath);
      }
    }
  }

  walk(apiRoot, "");
  return found.sort();
}

function routeFile(route: string): string {
  const ts = resolve(WEB_ROOT, "app", "api", route, "route.ts");
  const tsx = resolve(WEB_ROOT, "app", "api", route, "route.tsx");
  try {
    return readFileSync(ts, "utf8");
  } catch {
    return readFileSync(tsx, "utf8");
  }
}

function parseRadonCapability(source: string, route: string): RouteCapability {
  const match = source.match(
    /export\s+const\s+radonCapability(?:\s*:\s*[^=;]+)?\s*=\s*([^;]+);/,
  );
  expect(match, `${route} must export const radonCapability`).not.toBeNull();
  const expr = match![1].trim().replace(/\s+as const$/, "").trim();
  const asString = expr.match(
    /^["'](read|read\.spawn|mutate\.workspace|mutate\.trading|admin|internal)["']$/,
  );
  if (asString) return asString[1] as Capability;

  expect(expr.startsWith("{") && expr.endsWith("}"), `${route} capability expr`).toBe(true);
  const map: Record<string, Capability> = {};
  const pair =
    /\b(GET|POST|PUT|PATCH|DELETE|HEAD)\s*:\s*["'](read|read\.spawn|mutate\.workspace|mutate\.trading|admin|internal)["']/g;
  let hit: RegExpExecArray | null;
  while ((hit = pair.exec(expr))) {
    map[hit[1]] = hit[2] as Capability;
  }
  expect(Object.keys(map).length, `${route} method map`).toBeGreaterThan(0);
  return map;
}

function samePin(actual: RouteCapability, expected: PinnedCapability): boolean {
  if (typeof expected === "string") return actual === expected;
  if (typeof actual === "string") return false;
  const actualKeys = Object.keys(actual).sort();
  const expectedKeys = Object.keys(expected).sort();
  if (actualKeys.join() !== expectedKeys.join()) return false;
  return expectedKeys.every((key) => actual[key as keyof typeof actual] === expected[key]);
}

describe("assistant catalog pin", () => {
  it("keeps the capability helper tiny and refused-set exact", () => {
    const src = readFileSync(resolve(WEB_ROOT, "lib/assistant/capabilities.ts"), "utf8");
    expect(src).not.toMatch(/radonFetch|openapi/i);
    expect(isRefused("admin")).toBe(true);
    expect(isRefused("internal")).toBe(true);
    expect(isRefused("mutate.trading")).toBe(true);
    expect(isRefused("read")).toBe(false);
    expect(isRefused("read.spawn")).toBe(false);
    expect(isRefused("mutate.workspace")).toBe(false);
  });

  it("classifies every route file on disk (filesystem pin)", () => {
    const disk = collectApiRoutesFromFilesystem();
    const classified = new Set(Object.keys(PINNED));

    const unclassified = disk.filter((route) => !classified.has(route));
    expect(
      unclassified,
      "new API route files must export radonCapability and land in PINNED",
    ).toEqual([]);

    const stale = [...classified].filter((route) => !disk.includes(route)).sort();
    expect(stale, "classified routes that no longer exist on disk").toEqual([]);
  });

  it("every disk route exports a parseable radonCapability matching PINNED", () => {
    for (const route of collectApiRoutesFromFilesystem()) {
      const source = routeFile(route);
      expect(source, route).toMatch(/export\s+const\s+radonCapability/);
      const parsed = parseRadonCapability(source, route);
      const expected = PINNED[route];
      expect(expected, `PINNED missing ${route}`).toBeDefined();
      expect(samePin(parsed, expected), `${route} capability`).toBe(true);

      if (typeof parsed === "string") {
        expect(CAPABILITY_SET.has(parsed), route).toBe(true);
      } else {
        for (const cap of Object.values(parsed)) {
          expect(CAPABILITY_SET.has(cap), `${route} ${cap}`).toBe(true);
        }
      }
    }
  });

  it("pins assistant, pi, webhooks, and share HTML as internal", () => {
    expect(PINNED.assistant).toBe("internal");
    expect(PINNED.pi).toBe("internal");
    expect(PINNED["webhooks/clerk"]).toBe("internal");
    expect(PINNED["gex/share/content"]).toBe("internal");
    expect(PINNED["internals/share/content"]).toBe("internal");
    expect(PINNED["menthorq/cta/share/content"]).toBe("internal");
    expect(PINNED["regime/share/content"]).toBe("internal");
    expect(PINNED["vcg/share/content"]).toBe("internal");
    expect(PINNED["share/pnl"]).toBe("internal");
  });

  it("pins admin and ib operator routes as admin", () => {
    expect(PINNED["admin/demo-users"]).toEqual({ GET: "admin", POST: "admin" });
    expect(PINNED["admin/ib/restart"]).toBe("admin");
    expect(PINNED["admin/ib/reset-backoff"]).toBe("admin");
    expect(PINNED["admin/services"]).toBe("admin");
    expect(PINNED["admin/trading/[action]"]).toEqual({ GET: "admin", POST: "admin" });
    expect(PINNED["ib/ws-ticket"]).toBe("admin");
  });

  it("pins live order placement as mutate.trading", () => {
    expect(PINNED["orders/place"]).toBe("mutate.trading");
    expect(PINNED["orders/cancel"]).toBe("mutate.trading");
    expect(PINNED["orders/modify"]).toBe("mutate.trading");
    expect(PINNED["paper/place"]).toBe("mutate.trading");
  });

  it("pins watchlist GET read and POST/DELETE mutate.workspace", () => {
    expect(PINNED.watchlist).toEqual({ GET: "read", POST: "mutate.workspace" });
    expect(PINNED["watchlist/[symbol]"]).toBe("mutate.workspace");
  });
});
