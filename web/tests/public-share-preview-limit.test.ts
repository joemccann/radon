import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest, type NextFetchEvent } from "next/server";
import middleware, { PUBLIC_SHARE_API_ROUTES, isPublicRequest } from "../middleware";

const token = "preview-budget-local-test";
function request(route: string, forwardedFor: string, method = "GET") {
  return new NextRequest(`http://localhost:3000${route}`, {
    method,
    headers: { "x-forwarded-for": forwardedFor, "x-radon-authless-test": token },
  });
}

describe("public share preview request budget", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubEnv("RADON_AUTHLESS_TEST", "1");
    vi.stubEnv("RADON_AUTHLESS_TEST_TOKEN", token);
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs(); });

  it("shares a 60/minute budget across all six GET/HEAD previews and ignores spoofed left hops", async () => {
    for (let n = 0; n < 60; n++) {
      const route = PUBLIC_SHARE_API_ROUTES[n % PUBLIC_SHARE_API_ROUTES.length];
      const response = await middleware(request(route, `192.0.2.${n}, 198.51.100.70`, n % 2 ? "HEAD" : "GET"), {} as NextFetchEvent);
      expect(response?.status).toBe(200);
    }
    const blocked = await middleware(request(PUBLIC_SHARE_API_ROUTES[0], "203.0.113.99, 198.51.100.70"), {} as NextFetchEvent);
    expect(blocked?.status).toBe(429);
    expect(blocked?.headers.get("retry-after")).toBe("60");
    expect(blocked?.headers.get("cache-control")).toContain("no-store");
    const other = await middleware(request(PUBLIC_SHARE_API_ROUTES[0], "198.51.100.71"), {} as NextFetchEvent);
    expect(other?.status).toBe(200);
    vi.advanceTimersByTime(60_001);
    const reset = await middleware(request(PUBLIC_SHARE_API_ROUTES[0], "198.51.100.70"), {} as NextFetchEvent);
    expect(reset?.status).toBe(200);
  });

  it("keeps preview reads public and does not exempt writes or unknown scopes", () => {
    for (const route of PUBLIC_SHARE_API_ROUTES) {
      expect(isPublicRequest(request(route, "198.51.100.72"))).toBe(true);
      expect(isPublicRequest(request(route, "198.51.100.72", "POST"))).toBe(false);
    }
    expect(isPublicRequest(request("/api/orders/share/content", "198.51.100.72"))).toBe(false);
  });
});
