/**
 * POST /api/regime/share forwards the page's live CRI reading to FastAPI.
 * 2026-11-19: the page showed CRI 51 from live ticks; the cards showed the
 * 12:00 scan snapshot (48).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const radonFetch = vi.fn();
vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: vi.fn(async () => ({ ok: true, principal: { token: "jwt" } })),
}));
vi.mock("@/lib/rateLimit", () => ({
  rateLimit: () => ({ ok: true }),
  clientIp: () => "1.1.1.1",
  SHARE_CARD_LIMIT: 10,
  SHARE_WINDOW_MS: 60_000,
}));
vi.mock("@/lib/radonApi", () => ({
  radonFetch: (...args: unknown[]) => radonFetch(...args),
  RadonApiError: class RadonApiError extends Error { status = 502; },
}));

const LIVE = { vix: 28.54, cri: { score: 51, level: "HIGH", components: { vix: 16.5, vvix: 12.8, correlation: 7.4, momentum: 14.3 } } };

function post(body?: string) {
  return new Request("http://localhost/api/regime/share", {
    method: "POST",
    ...(body === undefined ? {} : { body, headers: { "Content-Type": "application/json" } }),
  });
}

describe("POST /api/regime/share live reading", () => {
  beforeEach(() => {
    radonFetch.mockReset().mockResolvedValue({ preview_path: "data/share_cards/x.html" });
  });

  it("forwards the reading as the JSON body", async () => {
    const { POST } = await import("../app/api/regime/share/route");
    const res = await POST(post(JSON.stringify({ live: LIVE })));
    expect(res.status).toBe(200);
    expect(radonFetch).toHaveBeenCalledExactlyOnceWith("/regime/share", {
      method: "POST",
      token: "jwt",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(LIVE),
    });
  });

  it("sends no body when the page has no live reading", async () => {
    const { POST } = await import("../app/api/regime/share/route");
    await POST(post());
    expect(radonFetch).toHaveBeenCalledExactlyOnceWith("/regime/share", { method: "POST", token: "jwt" });
  });

  it("refuses malformed JSON without calling FastAPI", async () => {
    const { POST } = await import("../app/api/regime/share/route");
    const res = await POST(post("{not json"));
    expect(res.status).toBe(400);
    expect(radonFetch).not.toHaveBeenCalled();
  });
});
