/**
 * @clerk/nextjs 7.x clerkMiddleware auto-enables Frontend API proxying when
 * the REQUEST host ends with ".vercel.app" and the key is a live key
 * (dist/esm/server/clerkMiddleware.js: `shouldAutoProxy(requestUrl.hostname)`
 * → `frontendApiProxy = { enabled: true }`). Our Clerk instance does not
 * allow those hosts, so every proxied /__clerk/v1/environment answered
 * 400 host_invalid. The middleware must pass an explicit
 * `frontendApiProxy: { enabled: false }`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest, NextResponse, type NextFetchEvent } from "next/server";

// pk_live_<base64("clerk.radon.example$")>: a syntactically valid live key.
const LIVE_PK = `pk_live_${Buffer.from("clerk.radon.example$").toString("base64")}`;
const PROXY_URL = "https://radon-demo-abc123.vercel.app/__clerk/v1/environment";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  vi.doUnmock("@clerk/nextjs/server");
});

describe("Clerk .vercel.app auto-proxy", () => {
  it("the installed clerkMiddleware proxies a .vercel.app /__clerk request unless frontendApiProxy is disabled", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 400 }));
    vi.stubGlobal("fetch", fetchMock);
    const { clerkMiddleware } = await import("@clerk/nextjs/server");
    const keys = { publishableKey: LIVE_PK, secretKey: "sk_live_test" };
    const event = { waitUntil: () => {} } as unknown as NextFetchEvent;

    await clerkMiddleware(() => NextResponse.next(), keys)(new NextRequest(PROXY_URL), event);
    const proxiedDefault = fetchMock.mock.calls.some(([u]) => String(u instanceof Request ? u.url : u).endsWith("/v1/environment"));
    expect(proxiedDefault).toBe(true);

    fetchMock.mockClear();
    await clerkMiddleware(() => NextResponse.next(), {
      ...keys,
      frontendApiProxy: { enabled: false },
    })(new NextRequest(PROXY_URL), event).catch(() => null);
    const proxiedDisabled = fetchMock.mock.calls.some(([u]) => String(u instanceof Request ? u.url : u).endsWith("/v1/environment"));
    expect(proxiedDisabled).toBe(false);
  });

  it("web/middleware.ts disables the Frontend API auto-proxy", async () => {
    const calls: unknown[][] = [];
    vi.doMock("@clerk/nextjs/server", async (importOriginal) => {
      const actual = await importOriginal<typeof import("@clerk/nextjs/server")>();
      return {
        ...actual,
        clerkMiddleware: (...args: unknown[]) => {
          calls.push(args);
          return () => NextResponse.next();
        },
      };
    });
    await import("../middleware");
    expect(calls).toHaveLength(1);
    expect(calls[0][1]).toMatchObject({ frontendApiProxy: { enabled: false } });
  });
});
