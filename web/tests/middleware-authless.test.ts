import { afterEach, describe, expect, it, vi } from "vitest";
import type { NextFetchEvent } from "next/server";
import { NextRequest } from "next/server";

import middleware, {
  isAuthlessTestBypassEnabled,
} from "../middleware";

describe("isAuthlessTestBypassEnabled", () => {
  it("requires an explicit flag plus an unforgeable matching request token", () => {
    expect(isAuthlessTestBypassEnabled("secret", "secret", "1")).toBe(true);
  });

  it("fails closed for absent, wrong, or unconfigured tokens", () => {
    expect(isAuthlessTestBypassEnabled(null, "secret", "1")).toBe(false);
    expect(isAuthlessTestBypassEnabled("wrong", "secret", "1")).toBe(false);
    expect(isAuthlessTestBypassEnabled("secret", undefined, "1")).toBe(false);
    expect(isAuthlessTestBypassEnabled("secret", "secret", undefined)).toBe(false);
  });

  it("does not trust a spoofed Host header", () => {
    const request = new NextRequest("http://0.0.0.0:3000/portfolio", {
      headers: { host: "localhost:3000" },
    });
    expect(isAuthlessTestBypassEnabled(
      request.headers.get("x-radon-authless-test"),
      "secret",
      "1",
    )).toBe(false);
  });
});

describe("authless test bypass vs first-run setup gate", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("serves /portfolio to a valid authless test request even with no Clerk env (keyless runner)", async () => {
    // Keyless runner: both Clerk keys blank, wizard never completed → the
    // setup gate would 302 everything to /setup. The Playwright token must
    // win: e2e specs run exactly like this.
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("RADON_SETUP_COMPLETE", "");
    vi.stubEnv("RADON_AUTHLESS_TEST", "1");
    vi.stubEnv("RADON_AUTHLESS_TEST_TOKEN", "secret-token");

    const request = new NextRequest("http://localhost:3000/portfolio", {
      headers: { "x-radon-authless-test": "secret-token" },
    });
    const response = await middleware(request, {} as NextFetchEvent);

    expect(response?.status).toBe(200);
    expect(response?.headers.get("location")).toBeNull();
  });

  it("still setup-gates a keyless request WITHOUT the authless token", async () => {
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("RADON_SETUP_COMPLETE", "");
    vi.stubEnv("RADON_AUTHLESS_TEST", "1");
    vi.stubEnv("RADON_AUTHLESS_TEST_TOKEN", "secret-token");

    const request = new NextRequest("http://localhost:3000/portfolio");
    const response = await middleware(request, {} as NextFetchEvent);

    expect(response?.status).toBe(307);
    expect(response?.headers.get("location")).toContain("/setup");
  });

  it("still auth-misconfigured-gates a keyless request WITHOUT the authless token", async () => {
    // Wizard finished (RADON_SETUP_COMPLETE=1) but the process lost its Clerk
    // keys. The bypass exemption must not swallow this gate for ordinary
    // traffic: only a token-bearing Playwright request is exempt (T-435).
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("RADON_SETUP_COMPLETE", "1");
    vi.stubEnv("RADON_AUTHLESS_TEST", "1");
    vi.stubEnv("RADON_AUTHLESS_TEST_TOKEN", "secret-token");

    const request = new NextRequest("http://localhost:3000/portfolio");
    const response = await middleware(request, {} as NextFetchEvent);

    expect(response?.status).toBe(503);
    expect(await response?.text()).toContain("authentication keys are not loaded");
  });
});
