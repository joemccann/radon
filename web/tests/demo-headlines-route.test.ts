import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireRouteAccess: vi.fn(),
  cachedReadResult: vi.fn(async (_key: string, _ttl: number, load: () => Promise<unknown>) => ({
    value: await load(),
    staleWhileError: false,
  })),
}));

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/dbCache", () => ({ cachedReadResult: mocks.cachedReadResult }));

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
  mocks.requireRouteAccess.mockReset();
  mocks.requireRouteAccess.mockResolvedValue({
    ok: true,
    principal: { userId: "demo-user", kind: "demo" },
  });
  mocks.cachedReadResult.mockClear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("GET /api/headlines", () => {
  it("returns a bounded, oldest-first, no-store demo snapshot", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: [
        {
          id: "newest",
          time: "2026-09-04T18:51:31.000Z",
          important: 1,
          data: { content: "  Newest   print  " },
          impact: [{ symbol: "SPX", impact: "mixed" }],
        },
        {
          id: "oldest",
          time: "2026-09-04T18:50:31.000Z",
          important: 0,
          data: { title: "Older print" },
          impact: [],
        },
      ] }),
    }));
    const { GET } = await import("../app/api/headlines/route");

    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(body.items).toEqual([
      expect.objectContaining({ id: "oldest", content: "Older print", important: false }),
      expect.objectContaining({ id: "newest", content: "Newest print", important: true }),
    ]);
    expect(JSON.stringify(body)).not.toContain("mktnews.net");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/flash"), expect.objectContaining({
      cache: "no-store",
    }));
  });

  it("enforces the route-local identity gate before provider work", async () => {
    const denied = new Response(JSON.stringify({ error: "Unauthorized" }), { status: 401 });
    mocks.requireRouteAccess.mockResolvedValueOnce({ ok: false, response: denied });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("../app/api/headlines/route");

    const response = await GET();

    expect(response).toBe(denied);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("bounds ring, content, and impact fields", async () => {
    const { normalizeFlashSnapshot } = await import("../app/api/headlines/route");
    const rows = Array.from({ length: 55 }, (_, index) => ({
      id: `id-${index}`,
      time: null,
      important: false,
      data: { content: `print ${index} ${"x".repeat(2_100)}` },
      impact: Array.from({ length: 10 }, () => ({ symbol: "LONG-SYMBOL-OVER-16", impact: "bullish-impact-over-16" })),
    }));

    const items = normalizeFlashSnapshot({ data: rows });

    expect(items).toHaveLength(50);
    expect(items[0]?.id).toBe("id-49");
    expect(items.at(-1)?.id).toBe("id-0");
    expect(items[0]?.content).toHaveLength(2_000);
    expect(items[0]?.impact).toHaveLength(8);
    expect(items[0]?.impact[0]).toEqual({ symbol: "LONG-SYMBOL-OVER", impact: "bullish-impact-o" });
  });

  it("labels a stale-on-error cache fallback as degraded", async () => {
    mocks.cachedReadResult.mockResolvedValueOnce({
      value: [{
        kind: "headline",
        id: "stale",
        time: "2026-09-04T18:40:31.000Z",
        important: false,
        content: "Last known print",
        impact: [],
      }],
      staleWhileError: true,
    });
    const { GET } = await import("../app/api/headlines/route");

    const response = await GET();
    const body = await response.json();

    expect(body).toEqual(expect.objectContaining({ degraded: true }));
  });

  it("rejects a nonempty provider snapshot with no valid headlines", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: [{ id: "missing-content", data: {} }] }),
    }));
    const { GET } = await import("../app/api/headlines/route");

    const response = await GET();

    expect(response.status).toBe(503);
  });

  it("is unavailable outside the demo deployment", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "0");
    const { GET } = await import("../app/api/headlines/route");

    const response = await GET();

    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(mocks.requireRouteAccess).not.toHaveBeenCalled();
  });

  it("returns a generic no-store error when the provider fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    const { GET } = await import("../app/api/headlines/route");

    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(body).toEqual(expect.objectContaining({
      error: "Headlines temporarily unavailable",
      code: "UPSTREAM_ERROR",
    }));
  });
});
