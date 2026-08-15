import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
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
const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));

function source(path: string): string {
  return readFileSync(resolve(WEB_ROOT, path), "utf8");
}

describe("security report route-local authorization matrix", () => {
  it("guards every reported protected route before privileged work", () => {
    for (const route of ROUTES) {
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
