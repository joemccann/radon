/**
 * POST /api/previous-close — in-memory day cache (U7).
 *
 * A warm cache must not call Unusual Whales on a second POST for the same
 * symbol. IB-first is unchanged: a cold miss still opens the IB snapshot
 * socket before any UW fallback.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const wsConstructors: Array<{ url: string }> = [];

vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({
    userId: "user_test",
    getToken: async () => null,
  })),
}));

vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: vi.fn(async () => ({
    ok: true,
    principal: { userId: "test", kind: "test" },
  })),
}));

vi.mock("ws", () => {
  class FakeWebSocket {
    onError?: () => void;
    onClose?: () => void;
    constructor(url: string) {
      wsConstructors.push({ url });
      setTimeout(() => this.onError?.(), 0);
    }
    on(event: string, cb: () => void) {
      if (event === "error") this.onError = cb;
      if (event === "close") this.onClose = cb;
    }
    send(_payload: string) { /* IB unavailable in this fixture */ }
    close() { this.onClose?.(); }
  }
  return { WebSocket: FakeWebSocket };
});

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

function postRequest(symbol: string): Request {
  return new Request("http://localhost/api/previous-close", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols: [symbol] }),
  });
}

describe("POST /api/previous-close warm in-memory cache", () => {
  const originalUwToken = process.env.UW_TOKEN;

  beforeEach(() => {
    vi.resetModules();
    mockFetch.mockReset();
    wsConstructors.length = 0;
    process.env.UW_TOKEN = "test-uw-token";
    process.env.ALLOWED_USER_IDS = "user_test";
  });

  afterEach(() => {
    if (originalUwToken == null) delete process.env.UW_TOKEN;
    else process.env.UW_TOKEN = originalUwToken;
    vi.clearAllMocks();
  });

  it("second POST for the same symbol does not call unusualwhales when the cache is warm", async () => {
    const uwUrls: string[] = [];
    mockFetch.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("unusualwhales.com")) {
        uwUrls.push(url);
        return {
          ok: true,
          json: async () => ({ data: { previous_close: 41.25 } }),
        };
      }
      return { ok: false, json: async () => ({}) };
    });

    const { POST } = await import("@/app/api/previous-close/route");

    const first = await POST(postRequest("WARMU7"));
    expect(first.status).toBe(200);
    expect((await first.json()).closes.WARMU7).toBe(41.25);
    expect(wsConstructors.length).toBeGreaterThan(0);
    expect(uwUrls.length).toBeGreaterThan(0);
    const uwAfterFirst = uwUrls.length;
    const ibAfterFirst = wsConstructors.length;

    const second = await POST(postRequest("WARMU7"));
    expect(second.status).toBe(200);
    expect((await second.json()).closes.WARMU7).toBe(41.25);
    expect(uwUrls).toHaveLength(uwAfterFirst);
    expect(wsConstructors).toHaveLength(ibAfterFirst);
  });
});
