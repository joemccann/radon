import { afterEach, describe, expect, it, vi } from "vitest";

import { requireRouteAccess } from "@/lib/routeAccess";
import { __resetRateLimitForTests } from "@/lib/demo/rateLimit";

const request = new Request("https://app.radon.run/api/portfolio");

describe("requireRouteAccess", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    __resetRateLimitForTests();
  });
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

  it("does not apply a demo durable tier to an allowlisted operator by default", async () => {
    const durableRateLimitFn = vi.fn(async () => ({
      success: false,
      limit: 10,
      remaining: 0,
      reset: 0,
    }));

    const result = await requireRouteAccess(request, {
      rate: { key: "portfolio:route", limit: 20, windowMs: 60_000 },
    }, {
      authFn: async () => ({ userId: "operator" }),
      env: { NODE_ENV: "production", ALLOWED_USER_IDS: "operator" },
      rateLimitFn: () => ({ ok: true, retryAfterSec: 0 }),
      durableRateLimitFn,
    });

    expect(result.ok).toBe(true);
    expect(durableRateLimitFn).not.toHaveBeenCalled();
  });

  it("keeps active demo traffic inside the default durable tier", async () => {
    const now = Date.parse("2026-08-13T12:00:00Z");
    const durableRateLimitFn = vi.fn(async () => ({
      success: false,
      limit: 10,
      remaining: 0,
      reset: now + 9_000,
    }));

    const result = await requireRouteAccess(request, {
      rate: { key: "portfolio:route", limit: 20, windowMs: 60_000 },
    }, {
      authFn: async () => ({
        userId: "demo-user",
        sessionClaims: {
          metadata: {
            demoRole: "trial",
            demoTrialExpiresAt: "2026-08-14T12:00:00Z",
          },
        },
      }),
      env: { NEXT_PUBLIC_RADON_DEMO: "1" },
      now,
      rateLimitFn: () => ({ ok: true, retryAfterSec: 0 }),
      durableRateLimitFn,
    });

    expect(result.ok).toBe(false);
    expect(durableRateLimitFn).toHaveBeenCalledWith(
      "B",
      "route:portfolio:route:demo-user",
    );
    if (!result.ok) {
      expect(result.response.status).toBe(429);
      expect(result.response.headers.get("Retry-After")).toBe("9");
    }
  });

  it("does not apply an explicit demo tier to an allowlisted operator", async () => {
    const durableRateLimitFn = vi.fn(async () => ({ success: false, limit: 10, remaining: 0, reset: 0 }));
    const result = await requireRouteAccess(request, {
      rate: { key: "scan", limit: 10, windowMs: 60_000 },
      durableRateTier: "B",
    }, {
      authFn: async () => ({ userId: "operator" }),
      env: { NODE_ENV: "production", ALLOWED_USER_IDS: "operator" },
      durableRateLimitFn,
    });
    expect(result.ok).toBe(true);
    expect(durableRateLimitFn).not.toHaveBeenCalled();
  });

  it("admits an active demo user through operatorOnly on a demo-blockade route", async () => {
    // demo.radon.run runs with ALLOWED_USER_IDS absent by design; the order
    // routes carry their own demo blockade (paper path / explicit refusal),
    // which is unreachable if operatorOnly 403s every demo user first.
    const future = new Date(Date.now() + 86_400_000).toISOString();
    const result = await requireRouteAccess(request, {
      operatorOnly: true,
      demoBlockadeRoute: true,
    }, {
      authFn: async () => ({
        userId: "user-demo",
        sessionClaims: { metadata: { demoRole: "trial", demoTrialExpiresAt: future } },
      }),
      env: { NEXT_PUBLIC_RADON_DEMO: "1" },
    });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.principal.kind).toBe("demo");
  });

  it("rejects an expired demo user on a demo-blockade route", async () => {
    const past = new Date(Date.now() - 86_400_000).toISOString();
    const result = await requireRouteAccess(request, {
      operatorOnly: true,
      demoBlockadeRoute: true,
    }, {
      authFn: async () => ({
        userId: "user-demo",
        sessionClaims: { metadata: { demoRole: "trial", demoTrialExpiresAt: past } },
      }),
      env: { NEXT_PUBLIC_RADON_DEMO: "1" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(403);
  });

  it("keeps demo-blockade routes fail-closed off the demo deployment", async () => {
    const result = await requireRouteAccess(request, {
      operatorOnly: true,
      demoBlockadeRoute: true,
    }, {
      authFn: async () => ({ userId: "user-dev" }),
      env: {},
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(403);
  });

  it("keeps operatorOnly closed to demo users on routes without the blockade", async () => {
    const future = new Date(Date.now() + 86_400_000).toISOString();
    const result = await requireRouteAccess(request, { operatorOnly: true }, {
      authFn: async () => ({
        userId: "user-demo",
        sessionClaims: { metadata: { demoRole: "trial", demoTrialExpiresAt: future } },
      }),
      env: { NEXT_PUBLIC_RADON_DEMO: "1" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(403);
  });

  it("fails closed when the demo durable limiter errors", async () => {
    const future = new Date(Date.now() + 86_400_000).toISOString();
    const result = await requireRouteAccess(request, {
      rate: { key: "scan", limit: 10, windowMs: 60_000 },
      durableRateTier: "B",
    }, {
      authFn: async () => ({
        userId: "demo-user",
        sessionClaims: { metadata: { demoRole: "trial", demoTrialExpiresAt: future } },
      }),
      env: { NEXT_PUBLIC_RADON_DEMO: "1" },
      durableRateLimitFn: async () => { throw new Error("redis down"); },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(503);
  });
});
