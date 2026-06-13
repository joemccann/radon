/**
 * Regression: every `/api/*` route on `app.radon.run` must require a Clerk
 * session, with a narrow allowlist for share-card link previews, the
 * service-health banner data, and a pre-approved `/api/health` probe.
 *
 * The bug this guards against: prior to 2026-05-15 the middleware listed
 * `/api/(.*)` inside `publicRoutes`, leaving every API route world-callable.
 * `https://app.radon.run/api/options/chain?symbol=MSFT` returned full chain
 * JSON to any anonymous client.
 *
 * These tests exercise the route-matcher decision (the structural correctness
 * of the policy) rather than spinning up `clerkMiddleware` against a real
 * Clerk runtime. End-to-end validation happens via curl after deploy.
 */
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import {
  isApiPath,
  isProbeBearerRoute,
  isPublicRoute,
  PROBE_BEARER_API_ROUTES,
  PUBLIC_SHARE_API_ROUTES,
} from "../middleware";

function reqFor(pathname: string): NextRequest {
  return new NextRequest(`https://app.radon.run${pathname}`);
}

// Enumerate every Next.js route handler under web/app/api from disk. A route
// directory containing a `route.{ts,tsx,js,jsx}` file becomes a callable URL
// path; dynamic segments (`[ticker]`) are kept literal because the matcher
// decision is segment-structural, not value-dependent.
function collectApiRoutesFromFilesystem(): string[] {
  const apiRoot = join(__dirname, "..", "app", "api");
  const found: string[] = [];

  function walk(dir: string, urlPath: string): void {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        walk(join(dir, entry.name), `${urlPath}/${entry.name}`);
      } else if (/^route\.(ts|tsx|js|jsx)$/.test(entry.name)) {
        found.push(urlPath);
      }
    }
  }

  walk(apiRoot, "/api");
  return [...new Set(found)].sort();
}

// The ONLY API routes intentionally reachable without a Clerk session, beyond
// the share-card previews (PUBLIC_SHARE_API_ROUTES, pinned to disk in
// middleware-share-allowlist.test.ts). Everything else MUST be protected.
//   /api/service-health — dashboard banner / status-page poller, no session
//   /api/health         — pre-approved liveness probe
// Adding an entry here is a deliberate, reviewed perimeter decision.
const REVIEWED_PUBLIC_NON_SHARE_API_ROUTES = [
  "/api/service-health",
  "/api/health",
] as const;

// Full reviewed public allowlist: share previews + the two above. Bearer-gated
// probe routes are NOT public (they pass the bearer gate, not Clerk), so they
// are deliberately absent — a probe route must read as "protected" to
// isPublicRoute.
const REVIEWED_PUBLIC_API_ROUTES = new Set<string>([
  ...PUBLIC_SHARE_API_ROUTES,
  ...REVIEWED_PUBLIC_NON_SHARE_API_ROUTES,
]);

describe("isPublicRoute — explicit allowlist", () => {
  it("exempts the Clerk auth-flow page routes", () => {
    expect(isPublicRoute(reqFor("/sign-in"))).toBe(true);
    expect(isPublicRoute(reqFor("/sign-in/factor-one"))).toBe(true);
    expect(isPublicRoute(reqFor("/sign-up"))).toBe(true);
    expect(isPublicRoute(reqFor("/sign-up/verify"))).toBe(true);
  });

  it("exempts /api/share/<thing> for OG image renders", () => {
    expect(isPublicRoute(reqFor("/api/share/pnl"))).toBe(true);
    expect(isPublicRoute(reqFor("/api/share/pnl?ticker=AAPL&pnl=420"))).toBe(true);
  });

  it("exempts /api/<scope>/share and /api/<scope>/share/content", () => {
    const scopes = ["menthorq/cta", "vcg", "internals", "regime", "gex"];
    for (const scope of scopes) {
      expect(isPublicRoute(reqFor(`/api/${scope}/share`))).toBe(true);
      expect(isPublicRoute(reqFor(`/api/${scope}/share/content`))).toBe(true);
      expect(isPublicRoute(reqFor(`/api/${scope}/share/content?id=42`))).toBe(true);
    }
  });

  it("exempts /api/service-health (banner data)", () => {
    expect(isPublicRoute(reqFor("/api/service-health"))).toBe(true);
  });

  it("exempts /api/health (future liveness probe, pre-approved)", () => {
    expect(isPublicRoute(reqFor("/api/health"))).toBe(true);
  });

  // CORE DEFAULT-DENY MATRIX. Enumerate EVERY route.ts on disk and classify it:
  // it is EITHER in the reviewed public allowlist OR it must be protected (not
  // public). A new route that nobody deliberately classified lands in the
  // protected bucket and passes; a new route someone wrongly makes public-by-
  // pattern fails the "must be protected" branch. This replaces the old 35-entry
  // hardcoded list that was missing 35 of the 70 real routes — including all 10
  // destructive /api/admin/* operator routes.
  it("classifies every route.ts on disk as either reviewed-public or protected", () => {
    const onDisk = collectApiRoutesFromFilesystem();

    // Sanity floor: the walker must actually find the route tree, or every
    // per-route assertion below would pass vacuously.
    expect(onDisk.length).toBeGreaterThanOrEqual(60);

    const misclassified: string[] = [];
    for (const route of onDisk) {
      const reviewedPublic = REVIEWED_PUBLIC_API_ROUTES.has(route);
      const treatedPublic = isPublicRoute(reqFor(route));
      // A route is correctly classified iff its isPublicRoute verdict matches
      // its reviewed status. Reviewed-public must be public; everything else
      // (protected + bearer-gated probe routes) must NOT be public.
      if (reviewedPublic !== treatedPublic) {
        misclassified.push(
          `${route} — on disk, reviewedPublic=${reviewedPublic} but isPublicRoute=${treatedPublic}`,
        );
      }
    }

    expect(
      misclassified,
      `Routes whose perimeter classification doesn't match the reviewed allowlist.\n` +
        `If a route should be public, add it to REVIEWED_PUBLIC_API_ROUTES (share routes ` +
        `go through PUBLIC_SHARE_API_ROUTES in middleware.ts) with a security review.\n` +
        `If it should be protected, ensure isPublicRoute does NOT match it.\n` +
        misclassified.join("\n"),
    ).toEqual([]);
  });

  it("every /api/admin/* operator route on disk is protected (none public)", () => {
    // Hard pin on the destructive operator surface specifically: the old test
    // omitted all of these, so a regression making any admin route public would
    // have gone unnoticed. These mutate IB Gateway / systemd units.
    const adminRoutes = collectApiRoutesFromFilesystem().filter((p) =>
      p.startsWith("/api/admin/"),
    );
    expect(adminRoutes.length).toBeGreaterThanOrEqual(8);
    for (const route of adminRoutes) {
      expect(isPublicRoute(reqFor(route)), route).toBe(false);
      expect(REVIEWED_PUBLIC_API_ROUTES.has(route), route).toBe(false);
    }
  });

  it("DOES NOT exempt the previously-public API routes (with query strings)", () => {
    // Spot-check the original incident-class routes plus query-string variants,
    // which the filesystem walker can't synthesize.
    const protectedApi = [
      "/api/options/chain",
      "/api/options/chain?symbol=MSFT&expiry=20260605",
      "/api/journal",
      "/api/portfolio",
      "/api/orders/place",
      "/api/flow-analysis/MSFT",
      "/api/ticker/ratings",
      "/api/pi",
      "/api/flex-token",
      "/api/admin/ib/restart",
      "/api/admin/services/radon-api/restart",
    ];
    for (const path of protectedApi) {
      expect(isPublicRoute(reqFor(path)), path).toBe(false);
    }
  });

  it("DOES NOT exempt random non-share paths that happen to contain the word 'share'", () => {
    // The matcher anchors on `share` as a full path segment. Substrings
    // like `shared` or `share-something` must NOT slip through.
    expect(isPublicRoute(reqFor("/api/shared-data"))).toBe(false);
    expect(isPublicRoute(reqFor("/api/foo/share-thing"))).toBe(false);
    expect(isPublicRoute(reqFor("/api/shareable"))).toBe(false);
  });

  it("DOES NOT exempt the bearer-gated probe routes — they are gated, not public", () => {
    // DUR-16 (deliberate perimeter change): /api/probe/freshness is reachable
    // without a Clerk session but ONLY through the middleware's timing-safe
    // bearer gate (RADON_PROBE_FRESHNESS_TOKEN). Listing it in isPublicRoute
    // would skip the bearer check entirely, so it must stay OUT of the
    // public allowlist and IN the probe-bearer list.
    for (const route of PROBE_BEARER_API_ROUTES) {
      expect(isPublicRoute(reqFor(route)), route).toBe(false);
      expect(isProbeBearerRoute(route), route).toBe(true);
    }
  });

  it("DOES NOT exempt unknown probe-shaped scopes (default-deny)", () => {
    const unknownProbeScopes = [
      "/api/probe",
      "/api/probe/other",
      "/api/probe/freshness/extra",
      "/api/probes/freshness",
    ];
    for (const path of unknownProbeScopes) {
      expect(isPublicRoute(reqFor(path)), path).toBe(false);
      expect(isProbeBearerRoute(path), path).toBe(false);
    }
  });

  it("DOES NOT exempt page routes that aren't sign-in/sign-up", () => {
    expect(isPublicRoute(reqFor("/"))).toBe(false);
    expect(isPublicRoute(reqFor("/portfolio"))).toBe(false);
    expect(isPublicRoute(reqFor("/orders"))).toBe(false);
    expect(isPublicRoute(reqFor("/flow-analysis/MSFT"))).toBe(false);
  });
});

describe("isApiPath — API vs page branching", () => {
  it("identifies /api/* as API", () => {
    expect(isApiPath("/api/options/chain")).toBe(true);
    expect(isApiPath("/api/portfolio")).toBe(true);
    expect(isApiPath("/api/")).toBe(true);
    expect(isApiPath("/api")).toBe(true);
  });

  it("does not match page paths or paths that start with 'api' but aren't /api/", () => {
    expect(isApiPath("/")).toBe(false);
    expect(isApiPath("/portfolio")).toBe(false);
    expect(isApiPath("/sign-in")).toBe(false);
    expect(isApiPath("/apis-of-some-kind")).toBe(false);
    expect(isApiPath("/apiary")).toBe(false);
  });
});
