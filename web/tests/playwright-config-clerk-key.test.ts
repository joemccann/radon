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

  it("forces a pk_test_ key over an ambient pk_live_ key whenever it sets RADON_AUTHLESS_TEST=1 (T-482)", async () => {
    // middleware.ts throws at import when RADON_AUTHLESS_TEST === "1" and the
    // publishable key starts with pk_live_. The webServer env must therefore
    // never let an ambient live key through alongside the authless flag.
    vi.stubEnv(
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
      "pk_live_bWFsaWNpb3VzLWFtYmllbnQta2V5JA",
    );
    vi.resetModules();
    const config = (await import("../playwright.config")).default;
    const webServer = config.webServer;
    expect(webServer).toBeDefined();
    expect(Array.isArray(webServer)).toBe(false);
    const env = (webServer as { env?: Record<string, string> }).env ?? {};
    expect(env.RADON_AUTHLESS_TEST).toBe("1");
    // With the flag set, the key must be either unset or pinned to pk_test_.
    const key = env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
    if (key !== undefined && key !== "") {
      expect(key).toMatch(/^pk_test_/);
    }
    expect(key).not.toMatch(/^pk_live_/);
  });
});
