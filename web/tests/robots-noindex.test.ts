/**
 * Regression: the authenticated app (app/demo.radon.run) must never be
 * indexed — all marketing/SEO value lives on radon.run (site/). Google indexed
 * https://demo.radon.run/sign-in?redirect_url=... because the app served no
 * robots.txt (the path 404'd through the auth perimeter) and no noindex
 * signal anywhere.
 *
 * Four pins:
 *   1. app/robots.ts disallows every path for every crawler, with no sitemap.
 *   2. The share-card routes stay crawlable via Allow carve-outs — preview
 *      bots (Twitterbot, Slackbot) honor robots.txt, so a blanket Disallow: /
 *      would stop shared tweet cards from unfurling.
 *   3. /robots.txt is anonymously reachable — crawlers have no Clerk session,
 *      so the middleware must treat it as public instead of redirecting it to
 *      /sign-in (which is exactly the URL Google indexed).
 *   4. next.config.mjs emits X-Robots-Tag: noindex, nofollow on all routes,
 *      covering responses robots.txt rules alone don't (already-indexed URLs).
 */
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import robots from "../app/robots";
import {
  AUTHENTICATED_SHARE_GENERATOR_ROUTES,
  isPublicRoute,
  PUBLIC_SHARE_API_ROUTES,
} from "../middleware";

describe("app/robots.ts — disallow all crawlers except share cards", () => {
  it("disallows every path for every user agent, with no sitemap", () => {
    const output = robots();
    expect(output.rules).toEqual({
      userAgent: "*",
      allow: [...PUBLIC_SHARE_API_ROUTES],
      disallow: "/",
    });
    expect(output).not.toHaveProperty("sitemap");
  });

  it("carves out every public share-card route so link previews keep unfurling", () => {
    const output = robots();
    const rules = output.rules as { allow: string[] };
    for (const route of PUBLIC_SHARE_API_ROUTES) {
      expect(rules.allow).toContain(route);
    }
  });

  // Preview bots only ever GET a rendered card. The generator POSTs run a
  // report script on the trading host and now require a Clerk session, so
  // advertising them to crawlers would only publish a path that 401s.
  it("does not advertise the authenticated generator POST routes", () => {
    const rules = robots().rules as { allow: string[] };
    for (const route of AUTHENTICATED_SHARE_GENERATOR_ROUTES) {
      expect(rules.allow, route).not.toContain(route);
    }
  });
});

describe("middleware — /robots.txt is anonymously reachable", () => {
  it("treats /robots.txt as public", () => {
    expect(isPublicRoute(new NextRequest("https://demo.radon.run/robots.txt"))).toBe(true);
  });

  it("does not widen the exemption beyond the exact path", () => {
    expect(isPublicRoute(new NextRequest("https://demo.radon.run/robots.txt/extra"))).toBe(false);
    expect(isPublicRoute(new NextRequest("https://demo.radon.run/robots"))).toBe(false);
  });
});

describe("next.config.mjs — X-Robots-Tag noindex on all routes", () => {
  it("emits X-Robots-Tag: noindex, nofollow for every path", async () => {
    const { default: config } = await import("../next.config.mjs");
    const rows = await config.headers();
    expect(rows[0].source).toBe("/:path*");
    const headers = rows[0].headers as { key: string; value: string }[];
    const tag = headers.find((h) => h.key === "X-Robots-Tag");
    expect(tag?.value).toBe("noindex, nofollow");
  });
});
