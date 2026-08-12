/**
 * F9 (layer 3) — img-src must not be a bare `https:` wildcard.
 *
 * `img-src 'self' https: data: blob:` admits an image from ANY https host, so
 * an injected markdown image in an assistant answer (see
 * assistant-untrusted-knowledge.test.ts) had a live network egress path for
 * whatever account figures the answer carried. The directive is now scoped to
 * the hosts the app actually loads images from.
 *
 * Enumerated before tightening (grep of the repo):
 *   'self'                    — /_next/image optimized output + static assets
 *   https://media.radon.run   — newsfeed card images, profile avatars
 *                               (next.config.mjs images.remotePatterns)
 *   Clerk image + frontend    — user.imageUrl avatars, sign-in UI assets
 *   data:                     — operator avatar uploads (data URL), inline SVG
 *   blob:                     — share-card preview object URLs
 */
import { describe, expect, it } from "vitest";

import { buildCspWithNonce } from "../middleware";

function imgSrc(): string {
  const directive = buildCspWithNonce("TESTNONCE")
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("img-src "));
  expect(directive, "img-src directive must exist").toBeDefined();
  return directive as string;
}

describe("CSP img-src — host allowlist, no open https wildcard", () => {
  it("does not admit every https host", () => {
    const sources = imgSrc().split(/\s+/).slice(1);
    expect(sources).not.toContain("https:");
    expect(sources).not.toContain("*");
  });

  it("still admits the hosts the app actually loads images from", () => {
    const directive = imgSrc();
    expect(directive).toContain("'self'");
    expect(directive).toContain("https://media.radon.run");
    expect(directive).toContain("https://img.clerk.com");
    expect(directive).toContain("data:");
    expect(directive).toContain("blob:");
  });

  it("covers every remote image host next.config.mjs is willing to optimize", async () => {
    // The two lists are separate declarations of the same fact ("hosts this app
    // loads images from"). Divergence is silent at build time and shows up as a
    // broken avatar or newsfeed card in production, so pin them together.
    const directive = imgSrc();
    const { default: config } = (await import("../next.config.mjs")) as unknown as {
      default: { images: { remotePatterns: { protocol: string; hostname: string }[] } };
    };
    for (const { protocol, hostname } of config.images.remotePatterns) {
      expect(directive, `img-src must admit ${hostname}`).toContain(`${protocol}://${hostname}`);
    }
  });

  it("leaves the other directives alone", () => {
    const csp = buildCspWithNonce("TESTNONCE");
    expect(csp).toMatch(/default-src\s+'self'/);
    expect(csp).toMatch(/connect-src[^;]*wss:/);
    expect(csp).toMatch(/frame-ancestors\s+'none'/);
  });
});
