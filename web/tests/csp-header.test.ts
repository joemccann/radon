import { describe, expect, it } from "vitest";

/**
 * Verifies that next.config.mjs ships a Content-Security-Policy-Report-Only
 * header on every route and that it contains the key directives needed for
 * the app to function (Clerk, media CDN, WebSocket relay).
 *
 * Shipped as Report-Only intentionally — see next.config.mjs buildCsp() for
 * the enforcement plan. The test name prefix "csp-report-only" makes the
 * intent explicit so a future enforcer doesn't silently flip the header name
 * without updating the assertions here.
 */
describe("CSP Report-Only header (next.config.mjs)", () => {
  it("applies Content-Security-Policy-Report-Only to all routes", async () => {
    const { default: config } = await import("../next.config.mjs");
    const rows = await config.headers();

    // Must cover the catch-all route
    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ source: "/:path*" }),
      ]),
    );

    const headers = rows[0].headers as { key: string; value: string }[];
    const cspHeader = headers.find(
      (h) => h.key === "Content-Security-Policy-Report-Only",
    );

    expect(cspHeader, "CSP-Report-Only header must be present").toBeDefined();
    const value = cspHeader!.value;

    // Policy must restrict default fallback to same-origin
    expect(value).toMatch(/default-src\s+'self'/);

    // Scripts: 'self' with unsafe-inline/eval for Next.js runtime
    expect(value).toMatch(/script-src[^;]*'unsafe-inline'/);
    expect(value).toMatch(/script-src[^;]*'unsafe-eval'/);

    // Clerk auth domains present in script-src
    expect(value).toMatch(/script-src[^;]*clerk\.radon\.run/);

    // img-src is permissive but explicit (covers media.radon.run via https:)
    expect(value).toMatch(/img-src\s+'self'\s+https:/);

    // WebSocket relay (wss:) + Turso/UW API (https:) in connect-src
    expect(value).toMatch(/connect-src[^;]*wss:/);
    expect(value).toMatch(/connect-src[^;]*https:/);

    // Frame embedding blocked
    expect(value).toMatch(/frame-ancestors\s+'none'/);

    // Plugin objects disallowed
    expect(value).toMatch(/object-src\s+'none'/);

    // Base URI locked to self (prevents base-tag injection)
    expect(value).toMatch(/base-uri\s+'self'/);
  });

  it("does NOT ship an enforcing Content-Security-Policy header yet", async () => {
    // Enforcing CSP must be introduced deliberately after violation triage.
    // If this test fails it means someone flipped the header name — update
    // the Report-Only test above to match and add a comment about the flip.
    const { default: config } = await import("../next.config.mjs");
    const rows = await config.headers();
    const headers = rows[0].headers as { key: string; value: string }[];
    const enforcing = headers.find((h) => h.key === "Content-Security-Policy");
    expect(
      enforcing,
      "Enforcing CSP not expected yet — flip to Report-Only first, triage violations, then enforce",
    ).toBeUndefined();
  });

  it("existing baseline headers are untouched by CSP addition", async () => {
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
