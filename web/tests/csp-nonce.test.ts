import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import {
  buildCspWithNonce,
  generateNonce,
  withNonceCsp,
} from "../middleware";

/**
 * The Content-Security-Policy moved from a static Report-Only header in
 * next.config.mjs to an ENFORCED per-request nonce'd policy owned by
 * web/middleware.ts. These pins guard that flip:
 *
 *   1. The CSP value is built with a 'nonce-...' + Clerk-host script-src, and
 *      DROPS 'unsafe-inline'/'unsafe-eval' for scripts (the nonce covers Next's
 *      inline + framework scripts; Clerk's unnonced loader is admitted by host).
 *   2. The middleware response carries an ENFORCED `Content-Security-Policy`
 *      header (NOT `-Report-Only`) and forwards the matching nonce to the app.
 *   3. next.config.mjs no longer emits ANY CSP header (middleware owns it).
 */

function reqFor(pathname: string): NextRequest {
  return new NextRequest(`https://app.radon.run${pathname}`);
}

describe("buildCspWithNonce — enforced, nonce'd, Clerk-host-allowlisted", () => {
  it("script-src carries the nonce + Clerk hosts and no unsafe-inline", () => {
    const csp = buildCspWithNonce("ABC123");
    const scriptSrc = csp
      .split(";")
      .map((d) => d.trim())
      .find((d) => d.startsWith("script-src"));

    expect(scriptSrc).toBeDefined();
    expect(scriptSrc).toContain("'nonce-ABC123'");
    // Clerk's loader is a static unnonced <script src> → admitted by host, NOT
    // strict-dynamic (which would block it). See middleware comment.
    expect(scriptSrc).toContain("https://*.clerk.accounts.dev");
    // Clerk loads the Cloudflare Turnstile CAPTCHA as an unnonced <script src>
    // from this origin during sign-up; it must be admitted by host.
    expect(scriptSrc).toContain("https://challenges.cloudflare.com");
    expect(scriptSrc).not.toContain("'strict-dynamic'");
    expect(scriptSrc).not.toContain("'unsafe-inline'");
    expect(scriptSrc).not.toContain("'unsafe-eval'");
  });

  it("frame-src admits the Clerk hosts and the Turnstile CAPTCHA iframe", () => {
    const csp = buildCspWithNonce("ABC123");
    const frameSrc = csp
      .split(";")
      .map((d) => d.trim())
      .find((d) => d.startsWith("frame-src"));

    expect(frameSrc).toBeDefined();
    expect(frameSrc).toContain("https://*.clerk.accounts.dev");
    expect(frameSrc).toContain("https://challenges.cloudflare.com");
  });

  it("does NOT include 'unsafe-eval' by default", () => {
    expect(buildCspWithNonce("ABC123")).not.toContain("'unsafe-eval'");
  });

  it("preserves the non-script directives from the old policy", () => {
    const csp = buildCspWithNonce("ABC123");
    expect(csp).toMatch(/default-src\s+'self'/);
    expect(csp).toMatch(/style-src[^;]*'unsafe-inline'/);
    expect(csp).toMatch(/img-src\s+'self'\s+https:/);
    expect(csp).toMatch(/connect-src[^;]*wss:/);
    expect(csp).toMatch(/connect-src[^;]*https:/);
    expect(csp).toMatch(/frame-ancestors\s+'none'/);
    expect(csp).toMatch(/object-src\s+'none'/);
    expect(csp).toMatch(/base-uri\s+'self'/);
    // Clerk's blob: Web Worker must be admitted (worker-src falls back to script-src).
    expect(csp).toMatch(/worker-src\s+'self'\s+blob:/);
  });

  it("generateNonce yields a fresh value each call", () => {
    expect(generateNonce()).not.toBe(generateNonce());
  });
});

describe("withNonceCsp — middleware emits enforced CSP + matching nonce", () => {
  it("sets an ENFORCED Content-Security-Policy header, not Report-Only", () => {
    const res = withNonceCsp(reqFor("/"));
    const enforced = res.headers.get("Content-Security-Policy");

    expect(enforced, "enforced CSP header must be present").toBeTruthy();
    expect(res.headers.get("Content-Security-Policy-Report-Only")).toBeNull();
    expect(enforced).toMatch(/'nonce-[^']+'/);
    expect(enforced).toContain("https://*.clerk.accounts.dev");
  });

  it("forwards the same nonce to the app via the x-nonce request header", () => {
    const res = withNonceCsp(reqFor("/"));
    const csp = res.headers.get("Content-Security-Policy")!;
    const nonceInCsp = csp.match(/'nonce-([^']+)'/)?.[1];
    expect(nonceInCsp).toBeTruthy();

    // NextResponse.next({ request: { headers } }) stashes the overridden
    // request headers so Next forwards them downstream.
    const overridden = res.headers.get("x-middleware-override-headers");
    expect(overridden).toContain("x-nonce");
    expect(res.headers.get("x-middleware-request-x-nonce")).toBe(nonceInCsp);
  });
});

describe("next.config.mjs no longer ships any CSP header", () => {
  it("emits neither Content-Security-Policy nor the Report-Only variant", async () => {
    const { default: config } = await import("../next.config.mjs");
    const rows = await config.headers();
    const headers = rows[0].headers as { key: string; value: string }[];

    expect(
      headers.find((h) => h.key === "Content-Security-Policy-Report-Only"),
      "next.config must NOT emit Report-Only — middleware owns the enforced CSP",
    ).toBeUndefined();
    expect(
      headers.find((h) => h.key === "Content-Security-Policy"),
      "next.config must NOT emit an enforced CSP — middleware owns it",
    ).toBeUndefined();
  });

  it("leaves the other baseline security headers in place", async () => {
    const { default: config } = await import("../next.config.mjs");
    const rows = await config.headers();
    const headers = rows[0].headers as { key: string; value: string }[];
    const map = Object.fromEntries(headers.map((h) => [h.key, h.value]));

    expect(map["X-Frame-Options"]).toBe("DENY");
    expect(map["X-Content-Type-Options"]).toBe("nosniff");
    expect(map["Referrer-Policy"]).toBe("strict-origin-when-cross-origin");
    expect(map["Permissions-Policy"]).toContain("camera=()");
  });
});
