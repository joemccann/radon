import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROUTES = [
  "assistant", "attribution", "backtest/[strategy]", "blotter", "breadth",
  "cash-flows", "discover", "flow-analysis", "flow-analysis/[ticker]",
  "futures/chain", "gamma-rotation", "gex", "index-options/chain", "internals",
  "journal", "journal/sync", "knowledge/prior-evals", "knowledge/search", "leap",
  "leap/scan", "menthorq/[command]/image", "menthorq/cta", "menthorq/cta/image", "newsfeed/posts",
  "options/chain", "options/expirations", "options/exposure", "options/rv-ratio",
  "orders/cancel", "orders/modify", "orders/place", "orders", "orders/whatif",
  "paper/place", "performance", "pi", "portfolio", "preferences", "previous-close",
  "regime", "scanner", "scanner/strength", "scanner/strength/scan",
  "scanner/theta", "scanner/theta/scan", "short-availability/[ticker]",
  "ticker/info", "ticker/news", "ticker/ratings", "ticker/seasonality", "vcg",
  "workflow/run", "service-health",
] as const;

const SHARE_ROUTES = ["gex", "internals", "menthorq/cta", "regime", "vcg"] as const;
const ADMIN_ROUTES = ["edge-health", "health", "host-metrics", "reliability", "slo"] as const;

// Guarded routes outside the security-report ROUTES list — admin actions and
// the alerts store, all of which carry their own requireRouteAccess call.
const GUARDED_ADMIN_ACTION_ROUTES = [
  "admin/ib/reset-backoff", "admin/ib/restart", "admin/services",
  "admin/services/[unit]/[action]", "admin/stack/restart",
  "admin/trading/[action]", "alerts", "alerts/[id]",
] as const;

// Routes with NO route-local guard: the middleware default-deny perimeter is
// their only auth layer — a deliberate classification for read-only market
// data and user-scoped stores. A new route file is UNCLASSIFIED until it
// lands either here or in a guarded list above; the filesystem pin below
// fails until that decision is made (the publicShareRoutes.ts discipline).
const MIDDLEWARE_PERIMETER_ONLY_ROUTES = [
  "admin/demo-users", "bookmarks", "bookmarks/[post_id]", "bpi", "catalysts",
  // cor/skew2d/vol-cone/equibles-*: read-only market-data indicators (R-079
  // classification) — same posture as bpi/margin-debt/straddle.
  "cor", "credit-spread", "iei-hyg", "equibles-ats-venue-share", "equibles-cot-positioning",
  "equibles-filing-forensics", "equibles-short-crowding",
  "equibles-smart-money-13f",
  "flex-token", "flow-surprise", "futures-quote", "garch-convergence",
  "garch-convergence/scan", "ib/ws-ticket", "index-quote",
  "informed-flow/[ticker]", "ivrank", "llm-token-index", "margin-debt", "prices",
  "profile", "risk-free-rate", "skew", "skew2d", "straddle", "vixcor",
  "vol-cone", "watchlist", "watchlist/[symbol]", "workflow", "yield-curve",
  "yield-curve/live",
] as const;

// Classified by the filesystem pins in middleware-share-allowlist.test.ts
// (public share cards, webhooks, bearer-gated probes) — enumerated here only
// so the disk sweep proves every route file has exactly one home.
const PINNED_ELSEWHERE_ROUTES = [
  "gex/share/content", "internals/share/content", "menthorq/cta/share/content",
  "probe/freshness", "regime/share/content", "share/pnl", "vcg/share/content",
  "webhooks/clerk",
] as const;

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

function source(path: string): string {
  return readFileSync(resolve(WEB_ROOT, path), "utf8");
}

describe("security report route-local authorization matrix", () => {
  it("classifies every route file on disk (filesystem pin)", () => {
    const disk = collectApiRoutesFromFilesystem();
    const classified = new Set<string>([
      ...ROUTES,
      ...SHARE_ROUTES.map((route) => `${route}/share`),
      ...ADMIN_ROUTES.map((route) => `admin/${route}`),
      ...GUARDED_ADMIN_ACTION_ROUTES,
      ...MIDDLEWARE_PERIMETER_ONLY_ROUTES,
      ...PINNED_ELSEWHERE_ROUTES,
    ]);

    const unclassified = disk.filter((route) => !classified.has(route));
    expect(
      unclassified,
      "new API route files must be classified in this matrix (guarded or middleware-perimeter-only) before they ship",
    ).toEqual([]);

    const stale = [...classified].filter((route) => !disk.includes(route)).sort();
    expect(stale, "classified routes that no longer exist on disk").toEqual([]);
  });

  it("guards every reported protected route before privileged work", () => {
    for (const route of [...ROUTES, ...GUARDED_ADMIN_ACTION_ROUTES]) {
      const text = source(`app/api/${route}/route.${route.includes("image") ? "tsx" : "ts"}`);
      expect(text, route).toContain("requireRouteAccess");
      expect(text, route).toMatch(/const access = await requireRouteAccess/);
      expect(text, route).toMatch(/if \(!access\.ok\) return access\.response/);
    }
  });

  it("guards the admin page with the fail-closed operator allowlist", () => {
    const text = source("app/admin/page.tsx");
    expect(text).toContain("requireRouteAccess");
    expect(text).toContain("operatorOnly: true");
    expect(text).not.toContain("requireDemoAdmin");
  });

  it("guards every share generator independently of middleware", () => {
    for (const route of SHARE_ROUTES) {
      expect(source(`app/api/${route}/share/route.ts`), route).toContain("requireRouteAccess");
    }
  });

  it("uses operator-only authorization on all reported admin reads", () => {
    for (const route of ADMIN_ROUTES) {
      const text = source(`app/api/admin/${route}/route.ts`);
      expect(text, route).toContain("requireRouteAccess");
      expect(text, route).toContain("operatorOnly: true");
    }
  });

  it("requires operator capability and durable mutation budgets on live order routes", () => {
    for (const route of ["place", "cancel", "modify", "whatif"]) {
      const text = source(`app/api/orders/${route}/route.ts`);
      expect(text, route).toContain("operatorOnly: true");
      expect(text, route).toContain('durableRateTier: "C"');
      // R-080: these routes carry their own demo blockade downstream (paper
      // path / explicit refusal), so an active demo user on the demo
      // deployment must reach it instead of dying on operatorOnly.
      expect(text, route).toContain("demoBlockadeRoute: true");
    }
  });

  it("requires operator capability and durable budgets on account mutations", () => {
    for (const route of ["blotter", "flow-analysis", "journal", "journal/sync", "orders", "performance", "pi", "portfolio", "preferences"]) {
      const text = source(`app/api/${route}/route.ts`);
      expect(text, route).toContain("operatorOnly: true");
      expect(text, route).toContain('durableRateTier: "C"');
    }
  });
});
