/**
 * Contract pin (T-018): every API route that forwards a user's order to the
 * operator's LIVE IB account must consult the demo blockade FIRST.
 *
 * Same default-deny discipline as PUBLIC_SHARE_API_ROUTES / AUTH_EXEMPT_PATHS
 * (web/tests/middleware-share-allowlist.test.ts): the filesystem is walked, the
 * gated set is derived from what each route actually calls, and the result is
 * pinned against an explicit list. A new route that forwards to /orders/place,
 * /orders/modify, /orders/cancel or /orders/whatif therefore fails this test
 * until someone deliberately (a) adds it to the list and (b) wires the gate —
 * which is exactly how /orders/modify and /orders/cancel shipped ungated, with
 * modify's replaceOrder branch running a real cancel + place for demo users.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = join(__dirname, "..");
const API_ROOT = join(WEB_ROOT, "app", "api");

// FastAPI paths that act on the operator's live IB order book on a caller's
// behalf. `/orders/refresh` is deliberately ABSENT: it re-reads the operator's
// own book and routes no user order, so the read-side snapshot route must not
// be dragged through the blockade.
const LIVE_IB_ORDER_PATHS = new Set([
  "/orders/place",
  "/orders/modify",
  "/orders/cancel",
  "/orders/whatif",
]);

const EXPECTED_GATED_ROUTES = [
  "app/api/orders/cancel/route.ts",
  "app/api/orders/modify/route.ts",
  "app/api/orders/place/route.ts",
  "app/api/orders/whatif/route.ts",
];

const BLOCKADE_MODULE = "@/lib/demo/orderBlockade";
const BLOCKADE_CALL = "resolveDemoOrderDecision(";

// Matches radonFetch("/x"), radonFetch<T>("/x"), and multi-line call sites.
const RADON_FETCH_TARGET = /radonFetch\s*(?:<[^()]*>)?\s*\(\s*["'`]([^"'`]+)["'`]/g;

function routeFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) routeFiles(join(dir, entry.name), found);
    else if (/^route\.(ts|tsx|js|jsx)$/.test(entry.name)) found.push(join(dir, entry.name));
  }
  return found;
}

function webRelative(file: string): string {
  return relative(WEB_ROOT, file).split(sep).join("/");
}

function upstreamPaths(source: string): string[] {
  return [...source.matchAll(RADON_FETCH_TARGET)].map((match) => match[1]);
}

type GatedRoute = { path: string; source: string };

function gatedRoutes(): GatedRoute[] {
  return routeFiles(API_ROOT)
    .map((file) => ({ path: webRelative(file), source: readFileSync(file, "utf8") }))
    .filter((route) => upstreamPaths(route.source).some((p) => LIVE_IB_ORDER_PATHS.has(p)))
    .sort((a, b) => a.path.localeCompare(b.path));
}

describe("live IB order routes are gated by the demo blockade", () => {
  it("the set of routes reaching a live IB order path is exactly the pinned list", () => {
    expect(gatedRoutes().map((route) => route.path)).toEqual(EXPECTED_GATED_ROUTES);
  });

  it("every gated route imports and calls resolveDemoOrderDecision", () => {
    for (const { path, source } of gatedRoutes()) {
      expect(source, `${path} must import the blockade`).toContain(BLOCKADE_MODULE);
      expect(source, `${path} must call the blockade`).toContain(BLOCKADE_CALL);
    }
  });

  it("the blockade call precedes the first live IB call in every gated route", () => {
    for (const { path, source } of gatedRoutes()) {
      const gateAt = source.indexOf(BLOCKADE_CALL);
      const liveAt = Math.min(
        ...[...LIVE_IB_ORDER_PATHS]
          .map((upstream) => source.indexOf(`"${upstream}"`))
          .filter((index) => index >= 0),
      );
      expect(gateAt, `${path} must call the blockade`).toBeGreaterThan(-1);
      expect(gateAt, `${path} gates AFTER it can reach IB`).toBeLessThan(liveAt);
    }
  });
});
