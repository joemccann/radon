/**
 * Contract: playwright.config.ts must PIN a Clerk publishable key into the
 * webServer env, so whether e2e specs exercise the realtime socket does not
 * depend on ambient NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY / web/.env on the
 * machine running Playwright. The stub is a pk_test_ key, never a live one.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("playwright.config webServer env — Clerk publishable key", () => {
  it("pins a pk_test_ stub key even when the ambient env carries none", async () => {
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.resetModules();
    const config = (await import("../playwright.config")).default;
    const webServer = config.webServer;
    expect(webServer).toBeDefined();
    expect(Array.isArray(webServer)).toBe(false);
    const env = (webServer as { env?: Record<string, string> }).env ?? {};
    expect(env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY).toMatch(/^pk_test_/);
  });
});
