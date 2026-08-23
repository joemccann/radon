/**
 * Regression: the authenticated app (app/demo.radon.run) must never be
 * indexed — all marketing/SEO value lives on radon.run (site/). Google indexed
 * https://demo.radon.run/sign-in?redirect_url=... because the app served no
 * robots.txt (the path 404'd through the auth perimeter) and no noindex
 * signal anywhere.
 *
 * Five pins:
 *   1. Wildcard crawlers still see Disallow: / (plus share-card Allows),
 *      with no sitemap.
 *   2. Googlebot is allowed to crawl so it can recrawl trapped URLs and
 *      honor X-Robots-Tag: noindex. A blanket Disallow: / is exactly the
 *      GSC "Indexed, though blocked by robots.txt" trap.
 *   3. The share-card routes stay crawlable via Allow carve-outs — preview
 *      bots (Twitterbot, Slackbot) honor robots.txt, so a blanket Disallow: /
 *      would stop shared tweet cards from unfurling.
 *   4. /robots.txt is anonymously reachable — crawlers have no Clerk session,
 *      so the middleware must treat it as public instead of redirecting it to
 *      /sign-in (which is exactly the URL Google indexed).
 *   5. next.config.mjs emits X-Robots-Tag: noindex, nofollow on all routes,
 *      covering responses robots.txt rules alone don't (already-indexed URLs).
 */
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import robots, { GOOGLE_INDEXING_BOTS } from "../app/robots";
import {
  AUTHENTICATED_SHARE_GENERATOR_ROUTES,
  isPublicRoute,
  PUBLIC_SHARE_API_ROUTES,
} from "../middleware";

type RobotsRule = {
  userAgent: string | string[];
  allow?: string | string[];
  disallow?: string | string[];
};

function robotsRules(): RobotsRule[] {
  const rules = robots().rules;
  return (Array.isArray(rules) ? rules : [rules]) as RobotsRule[];
}

function wildcardRule(): RobotsRule {
  const rule = robotsRules().find((entry) => entry.userAgent === "*");
  if (!rule) throw new Error("missing * robots rule");
  return rule;
}

describe("app/robots.ts — disallow all crawlers except share cards", () => {
  it("disallows every path for wildcard crawlers, with no sitemap", () => {
    expect(wildcardRule()).toEqual({
      userAgent: "*",
      allow: [...PUBLIC_SHARE_API_ROUTES],
      disallow: "/",
    });
    expect(robots()).not.toHaveProperty("sitemap");
  });

  it("lets Googlebot crawl so the noindex header can drop trapped URLs", () => {
    const google = robotsRules().find((rule) => {
      const ua = rule.userAgent;
      return Array.isArray(ua) && ua.includes("Googlebot");
    });
    expect(google).toBeDefined();
    expect(google!.userAgent).toEqual([...GOOGLE_INDEXING_BOTS]);
    expect(google!.allow).toBe("/");
    expect(google!.disallow).toBeUndefined();
  });

  it("carves out every public share-card route so link previews keep unfurling", () => {
    const allow = wildcardRule().allow;
    const allowed = Array.isArray(allow) ? allow : [allow];
    for (const route of PUBLIC_SHARE_API_ROUTES) {
      expect(allowed).toContain(route);
    }
  });

  // Preview bots only ever GET a rendered card. The generator POSTs run a
  // report script on the trading host and now require a Clerk session, so
  // advertising them to crawlers would only publish a path that 401s.
  it("does not advertise the authenticated generator POST routes", () => {
    const allow = wildcardRule().allow;
    const allowed = Array.isArray(allow) ? allow : [allow];
    for (const route of AUTHENTICATED_SHARE_GENERATOR_ROUTES) {
      expect(allowed, route).not.toContain(route);
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
