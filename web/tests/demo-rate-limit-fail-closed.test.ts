import { afterEach, describe, expect, it, vi } from "vitest";

import { __resetRateLimitForTests, demoRateLimit } from "@/lib/demo/rateLimit";

afterEach(() => {
  vi.unstubAllEnvs();
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
});
