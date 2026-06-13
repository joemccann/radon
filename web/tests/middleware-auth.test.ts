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
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { isApiPath, isProbeBearerRoute, isPublicRoute, PROBE_BEARER_API_ROUTES } from "../middleware";

function reqFor(pathname: string): NextRequest {
  return new NextRequest(`https://app.radon.run${pathname}`);
}

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

  it("DOES NOT exempt the previously-public API routes", () => {
    const protectedApi = [
      "/api/options/chain",
      "/api/options/chain?symbol=MSFT&expiry=20260605",
      "/api/options/expirations",
      "/api/journal",
      "/api/portfolio",
      "/api/orders",
      "/api/orders/place",
      "/api/orders/cancel",
      "/api/orders/modify",
      "/api/blotter",
      "/api/cash-flows",
      "/api/discover",
      "/api/flow-analysis",
      "/api/flow-analysis/MSFT",
      "/api/gex",
      "/api/gamma-rotation",
      "/api/vcg",
      "/api/internals",
      "/api/regime",
      "/api/scanner",
      "/api/assistant",
      "/api/newsfeed/posts",
      "/api/ticker/info",
      "/api/ticker/news",
      "/api/ticker/ratings",
      "/api/ticker/seasonality",
      "/api/previous-close",
      "/api/prices",
      "/api/performance",
      "/api/menthorq/cta",
      "/api/ib/ws-ticket",
      "/api/risk-free-rate",
      "/api/attribution",
      "/api/pi",
      "/api/flex-token",
    ];
    for (const path of protectedApi) {
      expect(isPublicRoute(reqFor(path))).toBe(false);
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
