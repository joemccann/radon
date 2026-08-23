/**
 * F3 + F5 — the share-card GENERATOR POSTs are not link previews.
 *
 * `/api/{gex,vcg,regime,internals,menthorq/cta}/share` were listed in
 * PUBLIC_SHARE_API_ROUTES, so the middleware short-circuited Clerk for them.
 * Each handler proxies to FastAPI over loopback, where is_trusted_local_request
 * grants full server-to-server trust, so an ANONYMOUS internet caller could run
 * generate_*_share.py on the trading host with a 120s budget behind nothing but
 * an in-memory per-worker rate limit. The responses also handed back the
 * absolute reports/ path, the internal layout lib/shareReportPath.ts set out to
 * withhold.
 *
 * The contract this pins: CONTENT GETs public, GENERATOR POSTs authenticated.
 * Link-preview bots (Twitterbot, Slackbot, iMessage) only ever GET, so the
 * exemption that exists for them costs nothing when it is scoped to reads.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";

import robots from "../app/robots";
import * as perimeter from "../middleware";

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(`Radon API ${status}: ${detail}`);
      this.name = "RadonApiError";
      this.status = status;
      this.detail = detail;
    }
  },
}));

// Partial mock: the middleware under test imports createRouteMatcher from the
// same module, so only auth() is stubbed.
vi.mock("@clerk/nextjs/server", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@clerk/nextjs/server")>()),
  auth: vi.fn().mockResolvedValue({
    userId: "user_test",
    getToken: async () => "test-token",
  }),
}));

const GENERATOR_ROUTES = [
  "/api/gex/share",
  "/api/internals/share",
  "/api/menthorq/cta/share",
  "/api/regime/share",
  "/api/vcg/share",
];

const PREVIEW_ROUTES = [
  "/api/gex/share/content",
  "/api/internals/share/content",
  "/api/menthorq/cta/share/content",
  "/api/regime/share/content",
  "/api/share/pnl",
  "/api/vcg/share/content",
];

function reqFor(pathname: string, method = "GET"): NextRequest {
  return new NextRequest(`https://app.radon.run${pathname}`, { method });
}

describe("share perimeter — content GETs public, generator POSTs authenticated", () => {
  it("keeps every read-only preview route public (bots have no Clerk session)", () => {
    for (const route of PREVIEW_ROUTES) {
      expect(perimeter.isPublicRoute(reqFor(route)), route).toBe(true);
      expect(perimeter.isPublicRoute(reqFor(`${route}?path=x`)), route).toBe(true);
    }
  });

  it("does NOT exempt the generator POST routes from Clerk", () => {
    for (const route of GENERATOR_ROUTES) {
      expect(perimeter.isPublicRoute(reqFor(route, "POST")), route).toBe(false);
    }
  });

  it("enumerates the generator routes so the split is a reviewed decision", () => {
    expect([...perimeter.AUTHENTICATED_SHARE_GENERATOR_ROUTES].sort()).toEqual(
      [...GENERATOR_ROUTES].sort(),
    );
    for (const route of perimeter.AUTHENTICATED_SHARE_GENERATOR_ROUTES) {
      expect(
        (perimeter.PUBLIC_SHARE_API_ROUTES as readonly string[]).includes(route),
        route,
      ).toBe(false);
    }
  });

  it("treats a non-GET hit on a public preview path as non-public", () => {
    for (const route of PREVIEW_ROUTES) {
      expect(perimeter.isPublicRequest(reqFor(route, "GET")), route).toBe(true);
      expect(perimeter.isPublicRequest(reqFor(route, "HEAD")), route).toBe(true);
      expect(perimeter.isPublicRequest(reqFor(route, "POST")), route).toBe(false);
      expect(perimeter.isPublicRequest(reqFor(route, "DELETE")), route).toBe(false);
    }
  });

  it("leaves the non-share public exemptions alone (any method)", () => {
    expect(perimeter.isPublicRequest(reqFor("/api/webhooks/clerk", "POST"))).toBe(true);
    expect(perimeter.isPublicRequest(reqFor("/api/service-health"))).toBe(false);
    expect(perimeter.isPublicRequest(reqFor("/sign-in", "POST"))).toBe(true);
    expect(perimeter.isPublicRequest(reqFor("/api/portfolio"))).toBe(false);
  });

  it("advertises only the preview GET paths to crawlers", () => {
    const rules = robots().rules;
    const list = Array.isArray(rules) ? rules : [rules];
    const wildcard = list.find((rule) => rule.userAgent === "*") as {
      allow: string[];
    };
    for (const route of PREVIEW_ROUTES) {
      expect(wildcard.allow, route).toContain(route);
    }
    for (const route of GENERATOR_ROUTES) {
      expect(wildcard.allow, route).not.toContain(route);
    }
  });
});

describe("share generator responses — no absolute reports/ path disclosure", () => {
  const ABSOLUTE_PREVIEW = "/home/radon/radon/reports/tweet-gex-2026-08-11.html";

  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  const routes: Array<[string, string]> = [
    ["gex", "../app/api/gex/share/route"],
    ["vcg", "../app/api/vcg/share/route"],
    ["regime", "../app/api/regime/share/route"],
    ["internals", "../app/api/internals/share/route"],
    ["cta", "../app/api/menthorq/cta/share/route"],
  ];

  for (const [label, modulePath] of routes) {
    it(`${label}: strips the host filesystem layout out of the response`, async () => {
      mockRadonFetch.mockResolvedValueOnce({
        preview_path: ABSOLUTE_PREVIEW,
        card_paths: [`/home/radon/radon/reports/tweet-${label}-2026-08-11-card-1.html`],
        png_paths: [`/home/radon/radon/reports/tweet-${label}-2026-08-11-card-1.png`],
        date: "2026-08-11",
        stale: true,
        data_date: "2026-08-08",
      });

      const { POST } = await import(modulePath);
      const res = await POST(new Request("https://app.radon.run", { method: "POST" }));
      const body = (await res.json()) as Record<string, unknown>;

      expect(JSON.stringify(body)).not.toContain("/home/radon");
      expect(body.card_paths).toBeUndefined();
      expect(body.png_paths).toBeUndefined();
      // The preview fetch still works: the modal hands preview_path straight to
      // the content GET, which resolves it against the same cwd.
      expect(body.preview_path).toBe("../reports/tweet-gex-2026-08-11.html");
      // Non-path fields (the stale-data notice) pass through untouched.
      expect(body.stale).toBe(true);
      expect(body.data_date).toBe("2026-08-08");
    });
  }
});
