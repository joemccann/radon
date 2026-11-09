import { describe, expect, it, vi } from "vitest";

const mockRequireRouteAccess = vi.fn(async () => ({
  ok: false as const,
  response: new Response(null, { status: 429 }),
}));
vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mockRequireRouteAccess }));

describe("GET /api/options/chain rate budget", () => {
  it("allows 120 reads per minute per user so the expiry prefetch never 429s", async () => {
    const { GET } = await import("../app/api/options/chain/route");
    const res = await GET(new Request("http://localhost/api/options/chain?symbol=NVDA&expiry=20280121"));
    expect(res.status).toBe(429);
    expect(mockRequireRouteAccess).toHaveBeenCalledWith(undefined, {
      rate: { key: "options/chain:route", limit: 120, windowMs: 60_000 },
      durableRateTier: "A",
    });
  });
});
