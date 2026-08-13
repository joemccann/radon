import { describe, expect, it } from "vitest";

import { requireRouteAccess } from "@/lib/routeAccess";

const request = new Request("https://app.radon.run/api/portfolio");

describe("requireRouteAccess", () => {
  it("rejects a missing identity before route work", async () => {
    const result = await requireRouteAccess(request, {}, {
      authFn: async () => ({ userId: null }),
      env: {},
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(401);
  });

  it("does not turn a resolved signed-out test auth result into a test principal", async () => {
    const result = await requireRouteAccess(request, {}, {
      authFn: async () => ({ userId: null }),
      env: { NODE_ENV: "test" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(401);
  });

  it("rejects a non-allowlisted production identity", async () => {
    const result = await requireRouteAccess(request, {}, {
      authFn: async () => ({ userId: "user-attacker" }),
      env: { ALLOWED_USER_IDS: "user-operator", RADON_REQUIRE_OPERATOR_ALLOWLIST: "1" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(403);
  });

  it("fails closed for missing demo metadata", async () => {
    const result = await requireRouteAccess(request, {}, {
      authFn: async () => ({ userId: "user-demo", sessionClaims: {} }),
      env: { NEXT_PUBLIC_RADON_DEMO: "1" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(403);
  });

  it("permits an active demo identity for demo-safe routes", async () => {
    const future = new Date(Date.now() + 86_400_000).toISOString();
    const result = await requireRouteAccess(request, {}, {
      authFn: async () => ({
        userId: "user-demo",
        sessionClaims: { metadata: { demoRole: "trial", demoTrialExpiresAt: future } },
      }),
      env: { NEXT_PUBLIC_RADON_DEMO: "1" },
    });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.principal.kind).toBe("demo");
  });

  it("requires an affirmative allowlist for operator-only routes", async () => {
    const result = await requireRouteAccess(request, { operatorOnly: true }, {
      authFn: async () => ({ userId: "user-dev" }),
      env: {},
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(403);
  });

  it("rate limits an authenticated principal before delegated work", async () => {
    const deps = {
      authFn: async () => ({ userId: "user-operator" }),
      env: { ALLOWED_USER_IDS: "user-operator" },
      rateLimitFn: () => ({ ok: false, retryAfterSec: 9 }),
    };
    const result = await requireRouteAccess(request, { rate: { key: "scan", limit: 1, windowMs: 60_000 } }, deps);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.status).toBe(429);
      expect(result.response.headers.get("Retry-After")).toBe("9");
    }
  });

  it("enforces the durable principal budget in production", async () => {
    const durableRateLimitFn = async () => ({ success: false, limit: 10, remaining: 0, reset: 0 });
    const result = await requireRouteAccess(request, {
      rate: { key: "scan", limit: 10, windowMs: 60_000 },
      durableRateTier: "B",
    }, {
      authFn: async () => ({ userId: "operator" }),
      env: { NODE_ENV: "production", ALLOWED_USER_IDS: "operator" },
      durableRateLimitFn,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(429);
  });

  it("fails closed when the production durable limiter errors", async () => {
    const result = await requireRouteAccess(request, {
      rate: { key: "scan", limit: 10, windowMs: 60_000 },
    }, {
      authFn: async () => ({ userId: "operator" }),
      env: { NODE_ENV: "production", ALLOWED_USER_IDS: "operator" },
      durableRateLimitFn: async () => { throw new Error("redis down"); },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(503);
  });
});
