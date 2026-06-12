/**
 * Regression: the share-card exemption in `web/middleware.ts` must be an
 * EXPLICIT route list, not a pattern. The old matcher
 * `/^\/api(?:\/[^/]+)*\/share(?:\/.*)?$/` silently published any future
 * `/api/<anything>/share*` path the moment its route file landed — a new
 * scope shipping a share route would be world-callable with zero review.
 *
 * Three pins:
 *   1. Every enumerated share route IS public (link-preview bots have no
 *      Clerk session).
 *   2. Unknown share-shaped scopes are NOT public (default-deny).
 *   3. The allowlist matches the filesystem exactly — adding a share
 *      route.* under web/app/api without updating PUBLIC_SHARE_API_ROUTES
 *      fails this test, forcing a deliberate perimeter decision.
 *
 * Companion: web/tests/middleware-auth.test.ts pins the broader isPublicRoute
 * behavior (sign-in/sign-up, service-health, protected API deny-list).
 */
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { isPublicRoute, PUBLIC_SHARE_API_ROUTES } from "../middleware";

function reqFor(pathname: string): NextRequest {
  return new NextRequest(`https://app.radon.run${pathname}`);
}

function collectShareRoutesFromFilesystem(): string[] {
  const apiRoot = join(__dirname, "..", "app", "api");
  const found: string[] = [];

  function walk(dir: string, urlPath: string): void {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        walk(join(dir, entry.name), `${urlPath}/${entry.name}`);
      } else if (/^route\.(ts|tsx|js|jsx)$/.test(entry.name)) {
        if (urlPath.split("/").includes("share")) found.push(urlPath);
      }
    }
  }

  walk(apiRoot, "/api");
  return found.sort();
}

describe("PUBLIC_SHARE_API_ROUTES — explicit share allowlist", () => {
  it("every enumerated share route is public", () => {
    for (const route of PUBLIC_SHARE_API_ROUTES) {
      expect(isPublicRoute(reqFor(route)), route).toBe(true);
      expect(isPublicRoute(reqFor(`${route}?id=42`)), `${route}?id=42`).toBe(true);
    }
  });

  it("unknown share-shaped scopes are NOT public (default-deny)", () => {
    const unknownShareScopes = [
      "/api/journal/share",
      "/api/journal/share/content",
      "/api/portfolio/share",
      "/api/orders/share/content",
      "/api/admin/share",
      "/api/share",
      "/api/share/portfolio",
      "/api/share/pnl/extra",
    ];
    for (const path of unknownShareScopes) {
      expect(isPublicRoute(reqFor(path)), path).toBe(false);
    }
  });

  it("allowlist matches the share route files on disk exactly", () => {
    const onDisk = collectShareRoutesFromFilesystem();
    expect([...PUBLIC_SHARE_API_ROUTES].sort()).toEqual(onDisk);
  });
});
