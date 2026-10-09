/** Smoke tests for /api/index-options/chain (Phase 3 — VIX/SPX options). */
import { describe, it, expect, vi, beforeEach } from "vitest";

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(`Radon API ${status}: ${detail}`);
      this.name = "RadonApiError";
      this.status = status;
      this.detail = detail;
    }
  },
}));

beforeEach(() => {
  mockRadonFetch.mockReset();
});

function req(url: string, init: RequestInit = {}): Request {
  return new Request(url, init);
}

async function jsonOf(res: Response): Promise<unknown> {
  return res.json();
}

describe("GET /api/index-options/chain", () => {
  it("returns 400 when symbol missing", async () => {
    const { GET } = await import("../app/api/index-options/chain/route");
    const res = await GET(req("http://localhost/api/index-options/chain"));
    expect(res.status).toBe(400);
  });

  it("forwards symbol + expiry to FastAPI", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      symbol: "VIX",
      exchange: "CBOE",
      tradingClass: "VIX",
      expirations: ["20260616"],
      contracts: [{ conId: 1, strike: 20, right: "C" }],
      count: 1,
    });
    const { GET } = await import("../app/api/index-options/chain/route");
    const res = await GET(req("http://localhost/api/index-options/chain?symbol=vix&expiry=20260617"));
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.symbol).toBe("VIX");
    expect(mockRadonFetch).toHaveBeenCalledWith(
      expect.stringContaining("symbol=VIX"),
      expect.objectContaining({ timeout: 25_000 }),
    );
    expect(mockRadonFetch.mock.calls[0][0]).toContain("expiry=20260617");
  });

  it("returns 502 envelope when FastAPI fails", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { GET } = await import("../app/api/index-options/chain/route");
    const res = await GET(req("http://localhost/api/index-options/chain?symbol=VIX&expiry=20260617"));
    expect(res.status).toBe(502);
    expect(mockRadonFetch).toHaveBeenCalled();
  });

  it("rejects request without expiry", async () => {
    const { GET } = await import("../app/api/index-options/chain/route");
    const missing = await GET(req("http://localhost/api/index-options/chain?symbol=VIX"));
    expect(missing.status).toBe(400);
    const blank = await GET(req("http://localhost/api/index-options/chain?symbol=VIX&expiry="));
    expect(blank.status).toBe(400);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });
});
