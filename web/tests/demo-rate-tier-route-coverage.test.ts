import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { classifyRateTier } from "@/lib/demo/rateTier";
import type { DemoRateTier } from "@/lib/demo/rateLimit";

// EXPENSIVE_PATTERNS matched `/api/(vcg|gex|cri)/scan`, which never existed,
// so the real producer POSTs (gex, gamma-rotation, breadth, internals,
// options/rv-ratio, garch-convergence/scan, ...) fell through to write tier C:
// five a day, shared with paper orders. Every mutating route on disk is run
// through the middleware classifier here, so a new producer cannot ship into
// the order budget, and the middleware tier is cross-checked against the tier
// the handler's own requireRouteAccess guard declares.

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const WRITE_METHODS = ["POST", "PUT", "PATCH", "DELETE"] as const;

// Concrete values for dynamic segments: the classifier runs pre-routing on
// the raw pathname.
const SAMPLE_PARAMS: Record<string, string> = {
  "[action]": "restart",
  "[id]": "42",
  "[post_id]": "post-1",
  "[service]": "uw",
  "[symbol]": "AAPL",
  "[ticker]": "AAPL",
  "[token]": "tok",
  "[unit]": "radon-api.service",
};

// Every mutating method on disk -> the middleware tier a demo request pays.
const EXPECTED: Record<string, DemoRateTier> = {
  // Producers: recompute or render server-side (tier B).
  "POST /api/breadth": "B",
  "POST /api/discover": "B",
  "POST /api/flow-analysis/[ticker]": "B",
  "POST /api/gamma-rotation": "B",
  "POST /api/garch-convergence/scan": "B",
  "POST /api/gex": "B",
  "POST /api/gex/share": "B",
  "POST /api/internals": "B",
  "POST /api/internals/share": "B",
  "POST /api/knowledge/search": "B",
  "POST /api/leap/scan": "B",
  "POST /api/menthorq/cta/share": "B",
  "POST /api/options/rv-ratio": "B",
  "POST /api/regime": "B",
  "POST /api/regime/share": "B",
  "POST /api/scanner": "B",
  "POST /api/scanner/bounce/scan": "B",
  "POST /api/scanner/strength/scan": "B",
  "POST /api/scanner/theta/scan": "B",
  "POST /api/scanner/vol-skew-mr/scan": "B",
  "POST /api/vcg/share": "B",
  // A batch price read sent as POST, fanning out to up to 30 symbols, each
  // costing a quote plus a fallback, so it pays its own fan-out tier.
  "POST /api/previous-close": "M",
  // LLM routes share the AI budget.
  "POST /api/assistant": "D",
  "POST /api/newsfeed/explain": "D",
  "POST /api/newsfeed/share": "D",
  // Relay ticket reconnect burst.
  "POST /api/ib/ws-ticket": "E",
  // Account, order and store mutations (tier C).
  "POST /api/admin/demo-users": "C",
  "POST /api/admin/ib/operator-hold": "C",
  "POST /api/admin/ib/reset-backoff": "C",
  "POST /api/admin/ib/restart": "C",
  "POST /api/admin/services/[unit]/[action]": "C",
  "POST /api/admin/stack/restart": "C",
  "POST /api/admin/trading/[action]": "C",
  "PUT /api/admin/slm-review": "C",
  "POST /api/alerts": "C",
  "DELETE /api/alerts/[id]": "C",
  "POST /api/blotter": "C",
  "POST /api/bookmarks": "C",
  "DELETE /api/bookmarks/[post_id]": "C",
  "PUT /api/credentials/[service]": "C",
  "DELETE /api/credentials/[service]": "C",
  "POST /api/flow-analysis": "C",
  "POST /api/journal": "C",
  "POST /api/journal/sync": "C",
  "POST /api/newsfeed/research/feedback": "C",
  "POST /api/newsfeed/research/rules": "C",
  "POST /api/orders": "C",
  "POST /api/orders/cancel": "C",
  "POST /api/orders/modify": "C",
  "POST /api/orders/place": "C",
  "POST /api/orders/whatif": "C",
  "POST /api/paper/place": "C",
  "POST /api/performance": "C",
  "POST /api/pi": "C",
  "POST /api/portfolio": "C",
  "PUT /api/preferences": "C",
  "DELETE /api/preferences": "C",
  "POST /api/prices": "C",
  "PUT /api/profile": "C",
  "POST /api/setup/complete": "C",
  "POST /api/setup/status": "C",
  "POST /api/setup/validate": "C",
  "POST /api/watchlist": "C",
  "DELETE /api/watchlist/[symbol]": "C",
  "POST /api/webhooks/clerk": "C",
  "POST /api/webhooks/tradingview/[token]": "C",
};

// The handler's own guard spends a different tier than the middleware on
// purpose. Both are operator-only, so a demo principal is refused before the
// handler budget is reached; the middleware keeps them out of the shared AI
// budget because neither reaches an LLM provider.
const HANDLER_TIER_EXCEPTIONS = new Set<string>([
  "PUT /api/admin/slm-review",
  "POST /api/newsfeed/research/feedback",
  "POST /api/newsfeed/research/rules",
]);

type RouteFile = { route: string; file: string; source: string };

function collectRoutes(): RouteFile[] {
  const apiRoot = resolve(WEB_ROOT, "app", "api");
  const found: RouteFile[] = [];
  function walk(dir: string): void {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/^route\.tsx?$/.test(entry.name)) {
        const route = `/api/${relative(apiRoot, dir).split("\\").join("/")}`;
        found.push({ route, file: full, source: readFileSync(full, "utf8") });
      }
    }
  }
  walk(apiRoot);
  return found.sort((a, b) => a.route.localeCompare(b.route));
}

function exportedWriteMethods(source: string): string[] {
  return WRITE_METHODS.filter((method) =>
    new RegExp(`export\\s+(?:async\\s+)?(?:function|const)\\s+${method}\\b`).test(source),
  );
}

function concretePath(route: string): string {
  return route
    .split("/")
    .map((segment) => SAMPLE_PARAMS[segment] ?? segment)
    .join("/");
}

/** Tier the handler's guard declares for `method`, when unambiguous. */
function handlerTier(source: string, method: string): string | null {
  const start = source.search(
    new RegExp(`export\\s+(?:async\\s+)?(?:function|const)\\s+${method}\\b`),
  );
  const rest = source.slice(start + 1);
  const next = rest.search(/\nexport\s/);
  const body = next === -1 ? rest : rest.slice(0, next);
  const tiers = [...body.matchAll(/durableRateTier: "([A-Z])"/g)].map((m) => m[1]);
  return tiers.length > 0 ? tiers[0] : null;
}

describe("demo middleware rate tier for every mutating route", () => {
  const routes = collectRoutes();
  const onDisk = routes.flatMap(({ route, source }) =>
    exportedWriteMethods(source).map((method) => ({ key: `${method} ${route}`, method, route, source })),
  );

  it("finds the mutating routes to check", () => {
    expect(onDisk.length).toBeGreaterThan(50);
  });

  it("every mutating route on disk has an expected tier (filesystem pin)", () => {
    const keys = new Set(onDisk.map((entry) => entry.key));
    expect(onDisk.map((e) => e.key).filter((key) => !(key in EXPECTED))).toEqual([]);
    expect(Object.keys(EXPECTED).filter((key) => !keys.has(key))).toEqual([]);
  });

  it("the middleware classifier assigns each one its expected tier", () => {
    const wrong = onDisk
      .map(({ key, method, route }) => ({ key, got: classifyRateTier(method, concretePath(route)) }))
      .filter(({ key, got }) => got !== EXPECTED[key]);
    expect(wrong).toEqual([]);
  });

  it("matches the tier the handler's own guard declares", () => {
    const drift: string[] = [];
    for (const { key, method, source } of onDisk) {
      if (HANDLER_TIER_EXCEPTIONS.has(key)) continue;
      const declared = handlerTier(source, method);
      if (declared && declared !== EXPECTED[key]) drift.push(`${key}: handler ${declared}, middleware ${EXPECTED[key]}`);
    }
    expect(drift).toEqual([]);
  });

  it("handler-tier exceptions are not stale", () => {
    for (const key of HANDLER_TIER_EXCEPTIONS) {
      const entry = onDisk.find((e) => e.key === key);
      expect(entry, key).toBeDefined();
      expect(handlerTier(entry!.source, entry!.method), key).not.toBe(EXPECTED[key]);
    }
  });
});
