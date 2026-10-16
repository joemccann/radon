import { describe, expect, it, vi } from "vitest";

const mockRequireRouteAccess = vi.fn(async () => ({
  ok: false as const,
  response: new Response(null, { status: 429 }),
}));
vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mockRequireRouteAccess }));

describe("GET /api/portfolio rate budget", () => {
  it("allows 2000 reads per minute per user so multi-tab polling never 429s", async () => {
    const { GET } = await import("../app/api/portfolio/route");
    await GET(new Request("http://localhost/api/portfolio?include=entry-dates"));
    expect(mockRequireRouteAccess).toHaveBeenCalledWith(
      expect.any(Request),
      { rate: { key: "portfolio:route", limit: 2000, windowMs: 60_000 }, durableRateTier: "I" },
    );
  });
});
