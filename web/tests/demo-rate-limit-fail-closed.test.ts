import { afterEach, describe, expect, it, vi } from "vitest";

import {
  __resetRateLimitForTests,
  __setLimiterFactoryForTests,
  demoRateLimit,
} from "@/lib/demo/rateLimit";

afterEach(() => {
  vi.unstubAllEnvs();
  __setLimiterFactoryForTests(null);
  __resetRateLimitForTests();
});

describe("durable demo rate limiter configuration", () => {
  it("fails closed in production when Redis is missing", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("UPSTASH_REDIS_REST_URL", "");
    vi.stubEnv("UPSTASH_REDIS_REST_TOKEN", "");
    __resetRateLimitForTests();
    expect((await demoRateLimit("B", "user")).success).toBe(false);
  });

  it("permits isolated local tests without Redis", async () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    __resetRateLimitForTests();
    expect((await demoRateLimit("B", "user")).success).toBe(true);
  });

  it("sizes passive shell polling for three persistent workstation tabs", async () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    vi.stubEnv("UPSTASH_REDIS_REST_URL", "");
    vi.stubEnv("UPSTASH_REDIS_REST_TOKEN", "");
    __resetRateLimitForTests();

    expect(await demoRateLimit("I", "user")).toMatchObject({ limit: 60, success: true });
    expect(await demoRateLimit("J", "user")).toMatchObject({ limit: 50_000, success: true });
  });
});

describe("a dead Upstash instance denies, it does not crash the request", () => {
  it("returns a structured deny instead of throwing out of middleware", async () => {
    // An expired/deleted Redis makes limiter.limit() reject. Unhandled, that
    // throws inside web/middleware.ts and every demo /api/* call becomes an
    // opaque 500 rather than a 429 the client can act on.
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    __setLimiterFactoryForTests(() => ({
      limit: async () => {
        throw new Error("Upstash: WRONGPASS invalid or expired token");
      },
    }));
    const result = await demoRateLimit("A", "user_1");
    expect(result.success).toBe(false);
    expect(result.remaining).toBe(0);
  });
});
