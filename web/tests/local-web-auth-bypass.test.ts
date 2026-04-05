import { beforeEach, describe, expect, it, vi } from "vitest";

const clerkMiddlewareMock = vi.fn((handler: unknown) => handler);
const createRouteMatcherMock = vi.fn((patterns: string[]) => {
  const prefixes = patterns.map((pattern) => pattern.replace("(.*)", ""));
  return (request: { nextUrl: { pathname: string } }) =>
    prefixes.some((prefix) => request.nextUrl.pathname.startsWith(prefix));
});

vi.mock("@clerk/nextjs/server", () => ({
  clerkMiddleware: clerkMiddlewareMock,
  createRouteMatcher: createRouteMatcherMock,
}));

describe("web auth bypass middleware", () => {
  type MiddlewareHandler = (
    auth: { protect: () => Promise<void> | void },
    request: { nextUrl: { pathname: string } },
  ) => Promise<void> | void;

  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.unstubAllEnvs();
    Reflect.deleteProperty(process.env, "CLERK_JWKS_URL");
    Reflect.deleteProperty(process.env, "RADON_BYPASS_WEB_AUTH");
    Reflect.deleteProperty(process.env, "NODE_ENV");

    clerkMiddlewareMock.mockImplementation((handler: unknown) => handler);
    createRouteMatcherMock.mockImplementation((patterns: string[]) => {
      const prefixes = patterns.map((pattern) => pattern.replace("(.*)", ""));
      return (request: { nextUrl: { pathname: string } }) =>
        prefixes.some((prefix) => request.nextUrl.pathname.startsWith(prefix));
    });
  });

  it("protects local app routes by default when bypass is not explicitly enabled", async () => {
    const protect = vi.fn();
    const middleware = (await import("@/middleware")).default as unknown as MiddlewareHandler;

    await middleware({ protect }, { nextUrl: { pathname: "/kit" } });

    expect(protect).toHaveBeenCalledOnce();
  });

  it("does not protect local app routes when bypass is explicitly enabled", async () => {
    vi.stubEnv("RADON_BYPASS_WEB_AUTH", "1");

    const protect = vi.fn();
    const middleware = (await import("@/middleware")).default as unknown as MiddlewareHandler;

    await middleware({ protect }, { nextUrl: { pathname: "/kit" } });

    expect(protect).not.toHaveBeenCalled();
  });

  it("keeps public routes unprotected when Clerk is configured", async () => {
    vi.stubEnv("CLERK_JWKS_URL", "https://clerk.example/jwks");

    const protect = vi.fn();
    const middleware = (await import("@/middleware")).default as unknown as MiddlewareHandler;

    await middleware({ protect }, { nextUrl: { pathname: "/api/orders" } });

    expect(protect).not.toHaveBeenCalled();
  });

  it("ignores the bypass flag in production mode", async () => {
    vi.stubEnv("RADON_BYPASS_WEB_AUTH", "1");
    vi.stubEnv("NODE_ENV", "production");

    const protect = vi.fn();
    const middleware = (await import("@/middleware")).default as unknown as MiddlewareHandler;

    await middleware({ protect }, { nextUrl: { pathname: "/kit" } });

    expect(protect).toHaveBeenCalledOnce();
  });
});
