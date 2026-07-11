import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Tests for local dev WS ticket flow:
 * - wsTicket.ts always calls same-origin /api/ib/ws-ticket
 * - IBStatusContext builds authenticated URL with ticket param
 */

describe("wsTicket local routing", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    mockFetch.mockReset();
    globalThis.fetch = mockFetch as unknown as typeof fetch;
  });

  it("calls /api/ib/ws-ticket (same-origin, no env var needed)", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ ticket: "local-ticket-abc" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { getWsTicket } = await import("@/lib/wsTicket");
    const ticket = await getWsTicket("test-clerk-token");

    expect(ticket).toBe("local-ticket-abc");
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/ib/ws-ticket");
    expect(options.cache).toBe("no-store");
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it("does not use NEXT_PUBLIC_RADON_API_URL for ws-ticket", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ ticket: "ticket-123" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { getWsTicket } = await import("@/lib/wsTicket");
    await getWsTicket("token");

    const [url] = mockFetch.mock.calls[0];
    // Must be same-origin path, never an absolute URL
    expect(url).not.toMatch(/^https?:\/\//);
    expect(url).toBe("/api/ib/ws-ticket");
  });
});

describe("ws-ticket Next.js proxy route", () => {
  it("route file exists at expected path", async () => {
    // This is a build-time check — the route must exist for Next.js to serve it
    const route = await import("@/app/api/ib/ws-ticket/route");
    expect(route.POST).toBeDefined();
    expect(typeof route.POST).toBe("function");
  });
});
