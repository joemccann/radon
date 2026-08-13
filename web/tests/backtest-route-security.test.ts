import { beforeEach, describe, expect, it, vi } from "vitest";

const radonFetch = vi.fn();
let userId: string | null = "user_test";

vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId })),
}));
vi.mock("@/lib/radonApi", () => ({ radonFetch }));

describe("backtest route security boundary", () => {
  beforeEach(() => {
    vi.resetModules();
    radonFetch.mockReset();
    userId = "user_test";
  });

  it("rejects unknown strategies without delegating backend authority", async () => {
    const { GET } = await import("../app/api/backtest/[strategy]/route");
    const response = await GET(new Request("http://localhost/api/backtest/arbitrary"), {
      params: Promise.resolve({ strategy: "arbitrary" }),
    });

    expect(response.status).toBe(400);
    expect(radonFetch).not.toHaveBeenCalled();
  });

  it("rejects a missing identity without delegating backend authority", async () => {
    userId = null;
    const { GET } = await import("../app/api/backtest/[strategy]/route");
    const response = await GET(new Request("http://localhost/api/backtest/cri"), {
      params: Promise.resolve({ strategy: "cri" }),
    });

    expect(response.status).toBe(401);
    expect(radonFetch).not.toHaveBeenCalled();
  });

  it("delegates only a registered strategy for an authenticated principal", async () => {
    radonFetch.mockResolvedValueOnce({ status: "ok" });
    const { GET } = await import("../app/api/backtest/[strategy]/route");
    const response = await GET(new Request("http://localhost/api/backtest/cri"), {
      params: Promise.resolve({ strategy: "cri" }),
    });

    expect(response.status).toBe(200);
    expect(radonFetch).toHaveBeenCalledWith("/backtest/cri", {
      timeout: 190_000,
      signal: expect.any(AbortSignal),
    });
  });
});
