/**
 * POST /api/previous-close — bounded negative cache for unresolvable symbols.
 *
 * The day cache only remembers resolved closes, so a batch of well-formed but
 * unresolvable tickers re-issued the whole UW + Yahoo fan-out on every POST.
 * A short-lived miss record collapses the replay to one round of upstream
 * calls. IB stays first-source on every request (data-source priority), so
 * only the third-party fallbacks are suppressed.
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

function postRequest(symbols: string[]): Request {
  return new Request("http://localhost/api/previous-close", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols }),
  });
}

describe("POST /api/previous-close unresolvable-symbol replay", () => {
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

  it("does not re-issue the UW and Yahoo fan-out for a symbol no source resolves", async () => {
    const upstream: string[] = [];
    mockFetch.mockImplementation(async (input: RequestInfo | URL) => {
      upstream.push(String(input));
      return { ok: true, json: async () => ({}) };
    });

    const { POST } = await import("@/app/api/previous-close/route");

    const first = await POST(postRequest(["MISSU7"]));
    expect(first.status).toBe(200);
    expect((await first.json()).closes.MISSU7).toBeUndefined();
    expect(upstream.filter((u) => u.includes("unusualwhales.com"))).toHaveLength(1);
    expect(upstream.filter((u) => u.includes("finance.yahoo.com"))).toHaveLength(1);

    const second = await POST(postRequest(["MISSU7"]));
    expect(second.status).toBe(200);
    expect((await second.json()).closes.MISSU7).toBeUndefined();
    expect(upstream.filter((u) => u.includes("unusualwhales.com"))).toHaveLength(1);
    expect(upstream.filter((u) => u.includes("finance.yahoo.com"))).toHaveLength(1);
  });

  it("does not remember a miss when a source failed rather than answered", async () => {
    // A UW 429 or a Yahoo timeout is not "no source has this symbol". Caching
    // it would cost the previous close for the rest of the browser session on
    // a symbol a retry one second later resolves.
    const upstream: string[] = [];
    let uwFails = true;
    mockFetch.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      upstream.push(url);
      if (url.includes("unusualwhales.com")) {
        if (uwFails) return { ok: false, status: 429, json: async () => ({}) };
        return { ok: true, json: async () => ({ data: { previous_close: 31.5 } }) };
      }
      return { ok: true, json: async () => ({}) };
    });

    const { POST } = await import("@/app/api/previous-close/route");

    const first = await POST(postRequest(["FLAKEU7"]));
    expect((await first.json()).closes.FLAKEU7).toBeUndefined();

    uwFails = false;
    const second = await POST(postRequest(["FLAKEU7"]));
    expect((await second.json()).closes.FLAKEU7).toBe(31.5);
    expect(upstream.filter((u) => u.includes("unusualwhales.com"))).toHaveLength(2);
  });

  it("still resolves a symbol the sources can serve while a miss is remembered", async () => {
    const upstream: string[] = [];
    mockFetch.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      upstream.push(url);
      if (url.includes("unusualwhales.com") && url.includes("GOODU7")) {
        return { ok: true, json: async () => ({ data: { previous_close: 12.5 } }) };
      }
      return { ok: true, json: async () => ({}) };
    });

    const { POST } = await import("@/app/api/previous-close/route");

    await POST(postRequest(["MISSU7"]));
    const mixed = await POST(postRequest(["MISSU7", "GOODU7"]));
    expect(mixed.status).toBe(200);
    expect((await mixed.json()).closes).toEqual({ GOODU7: 12.5 });
    // Only the first round of third-party lookups; the localhost UW usage
    // recorder is not an upstream price source.
    const thirdParty = upstream.filter((u) => u.startsWith("https://") && u.includes("MISSU7"));
    expect(thirdParty).toHaveLength(2);
  });
});
