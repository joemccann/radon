import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Tests for Clerk auth integration in radonFetch and wsTicket.
 *
 * Covers:
 * - Authorization header injection when a token is provided
 * - Header omission when no token
 * - wsTicket API call shape and error handling
 */

describe("radonFetch with auth token", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    mockFetch.mockReset();
    globalThis.fetch = mockFetch as unknown as typeof fetch;
  });

  it("sends Authorization header when token is provided", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ data: "test" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { radonFetch } = await import("@/lib/radonApi");
    await radonFetch("/test", { token: "test-jwt-token" });

    const [, options] = mockFetch.mock.calls[0];
    const headers = new Headers(options.headers);
    expect(headers.get("Authorization")).toBe("Bearer test-jwt-token");
  });

  it("omits Authorization header when no token", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ data: "test" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { radonFetch } = await import("@/lib/radonApi");
    await radonFetch("/test");

    const [, options] = mockFetch.mock.calls[0];
    const headers = new Headers(options.headers);
    expect(headers.get("Authorization")).toBeNull();
  });
});

describe("wsTicket", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    mockFetch.mockReset();
    globalThis.fetch = mockFetch as unknown as typeof fetch;
  });

  it("calls the API with correct auth header", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ ticket: "uuid-ticket-123" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { getWsTicket } = await import("@/lib/wsTicket");
    const ticket = await getWsTicket("my-clerk-token");

    expect(ticket).toBe("uuid-ticket-123");
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain("/ws-ticket");
    expect(options.method).toBe("POST");
    expect(options.cache).toBe("no-store");
    expect(options.headers.Authorization).toBe("Bearer my-clerk-token");
  });

  it("throws on non-ok response", async () => {
    mockFetch.mockResolvedValue(
      new Response("Unauthorized", { status: 401 }),
    );

    const { getWsTicket } = await import("@/lib/wsTicket");
    await expect(getWsTicket("bad-token")).rejects.toThrow(
      "Failed to obtain WS ticket: 401",
    );
  });
});
