/**
 * REL-069 tranche A — R-179, R-180, R-181, R-182, R-186.
 *
 * Five holes in the auth perimeter the 2026-08-22 delta opened or left
 * mis-described: a loopback-trusted deputy, a subprocess-spawning POST filed
 * under "read-only market data", a guarded route filed under "no guard", an
 * admission that is undone two lines later, and an unrate-limited operator
 * diagnostic behind one shared static token.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const REPO = join(__dirname, "..", "..");
const read = (rel: string) => readFileSync(join(REPO, rel), "utf-8");

// ---------------------------------------------------------------------------
// R-179 — /api/ib/ws-ticket must not mint from an anonymous caller
// ---------------------------------------------------------------------------
describe("R-179: ws-ticket requires a caller identity", () => {
  it("has a route-local guard", () => {
    const src = read("web/app/api/ib/ws-ticket/route.ts");
    expect(src).toContain("requireRouteAccess");
  });

  it("does not forward an undefined token to the upstream mint", () => {
    const src = read("web/app/api/ib/ws-ticket/route.ts");
    // `token: undefined` makes radonFetch omit the header, and FastAPI then
    // treats the call as loopback-trusted — the Next.js server IS loopback.
    expect(src).not.toMatch(/^\s*token,\s*$/m);
    expect(src).not.toContain("? authHeader.slice");
  });

  it("mints from the authenticated principal's own token", () => {
    const src = read("web/app/api/ib/ws-ticket/route.ts");
    expect(src).toMatch(/access\.principal|principal\.token/);
  });

  it("is no longer classified as middleware-perimeter-only", () => {
    const src = read("web/tests/route-local-authz-matrix.test.ts");
    const bucket = src
      .split("const MIDDLEWARE_PERIMETER_ONLY_ROUTES = [")[1]
      .split("] as const;")[0];
    expect(bucket).not.toContain('"ib/ws-ticket"');
  });
});

// ---------------------------------------------------------------------------
// R-180 — a subprocess-spawning POST is not read-only market data
// ---------------------------------------------------------------------------
describe("R-180: the garch scan POST carries its own guard", () => {
  it("guards before it does anything else", () => {
    const src = read("web/app/api/garch-convergence/scan/route.ts");
    expect(src).toContain("requireRouteAccess");
    const body = src.split("export async function POST(")[1];
    const guardAt = body.indexOf("requireRouteAccess");
    const workAt = body.indexOf("radonFetch");
    expect(guardAt).toBeGreaterThanOrEqual(0);
    expect(guardAt).toBeLessThan(workAt);
  });

  it("rate-limits like its leap/scan sibling", () => {
    const src = read("web/app/api/garch-convergence/scan/route.ts");
    expect(src).toMatch(/rate:\s*\{/);
  });

  it("leaves the read-only bucket", () => {
    const src = read("web/tests/route-local-authz-matrix.test.ts");
    const bucket = src
      .split("const MIDDLEWARE_PERIMETER_ONLY_ROUTES = [")[1]
      .split("] as const;")[0];
    expect(bucket).not.toContain('"garch-convergence/scan"');
    // The read-only GET stays where it is.
    expect(bucket).toContain('"garch-convergence"');
  });
});

// ---------------------------------------------------------------------------
// R-181 — a route WITH a guard must not be filed under "no guard"
// ---------------------------------------------------------------------------
describe("R-181: admin/demo-users is classified by the guard it actually has", () => {
  it("still has requireDemoAdmin", () => {
    expect(read("web/app/api/admin/demo-users/route.ts")).toContain("requireDemoAdmin");
  });

  it("is not filed as middleware-perimeter-only", () => {
    const src = read("web/tests/route-local-authz-matrix.test.ts");
    const bucket = src
      .split("const MIDDLEWARE_PERIMETER_ONLY_ROUTES = [")[1]
      .split("] as const;")[0];
    expect(bucket).not.toContain('"admin/demo-users"');
  });

  it("is enumerated in a bucket that names its allowlist", () => {
    const src = read("web/tests/route-local-authz-matrix.test.ts");
    expect(src).toContain("DEMO_ADMIN_USER_IDS");
  });
});

// ---------------------------------------------------------------------------
// R-182 — the demo-blockade admission must survive the allowlist gate
// ---------------------------------------------------------------------------
describe("R-182: demoBlockadeRoute is not undone two lines later", () => {
  it("the allowlist gate honours the admission", () => {
    const src = read("web/lib/routeAccess.ts");
    const gate = src.split("const admittedByDemoBlockade")[1].split("const kind")[0];
    const allowlistLine = gate
      .split("\n")
      .find((line) => line.includes("allowed.size > 0"));
    expect(allowlistLine).toBeDefined();
    expect(allowlistLine).toContain("admittedByDemoBlockade");
  });

  // `deps` is the THIRD positional argument, not an options key — passing it
  // inside `options` silently falls through to the NODE_ENV=test bypass and
  // the gate is never exercised at all.
  it("an admitted demo principal reaches the route even with an allowlist set", async () => {
    const { requireRouteAccess } = await import("../lib/routeAccess");
    const access = await requireRouteAccess(
      undefined,
      { operatorOnly: true, demoBlockadeRoute: true },
      {
        authFn: async () => ({
          userId: "user_demo",
          sessionClaims: {
            metadata: {
              demoRole: "trial",
              demoTrialStartedAt: "2026-08-01T00:00:00Z",
              demoTrialExpiresAt: "2099-01-01T00:00:00Z",
            },
          },
        }),
        env: { NEXT_PUBLIC_RADON_DEMO: "1", ALLOWED_USER_IDS: "user_operator" },
      } as never,
    );
    expect(access.ok).toBe(true);
  });

  it("a non-demo caller is still rejected by the allowlist", async () => {
    const { requireRouteAccess } = await import("../lib/routeAccess");
    const access = await requireRouteAccess(
      undefined,
      { operatorOnly: true, demoBlockadeRoute: true },
      {
        authFn: async () => ({ userId: "user_stranger", sessionClaims: {} }),
        env: { ALLOWED_USER_IDS: "user_operator" },
      } as never,
    );
    expect(access.ok).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// R-186 — the probe bearer is one shared static token
// ---------------------------------------------------------------------------
describe("R-186: the probe path is rate-limited and narrowed", () => {
  it("rate-limits the bearer path", () => {
    const src = read("web/app/api/service-health/route.ts");
    const body = src.split("export async function GET(")[1];
    expect(body).toMatch(/probeRate|rateLimit|checkRate/i);
  });

  it("serves the probe a reduced payload, not every last_error", () => {
    const src = read("web/app/api/service-health/route.ts");
    expect(src).toMatch(/probeAuthorized/);
    // The probe needs states and freshness, not writer-supplied diagnostics.
    expect(src).toMatch(/probeView|probePayload|forProbe/);
  });

  it("an operator session still sees the full diagnostic payload", () => {
    const src = read("web/app/api/service-health/route.ts");
    expect(src).toContain("last_error");
    expect(src).toContain("error_summary");
  });
});
