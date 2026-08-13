import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/radonApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/radonApi")>();
  return {
    ...actual,
    radonFetch: mocks.radonFetch,
  };
});

const PAYLOAD = {
  schema_version: 1,
  symbol: "MU",
  source: "menthorq_dashboard",
  source_time: "2026-07-16T20:00:00",
  fetched_at: "2026-07-17T03:00:00Z",
  frequency: "eod",
  spot: 853.2,
  strikes: [850],
  expirations: [{ expiration_date: "2026-07-17", dte: 1 }],
  cells: {
    strike_idx: [0],
    expiration_idx: [0],
    net_gex: [-8_480_000],
    abs_gex: [8_480_000],
    net_dex: [-125_000],
    abs_dex: [125_000],
    oi_call: [100],
    oi_put: [420],
  },
  levels: [],
  units: {},
  complete: true,
};

describe("GET /api/options/exposure", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.radonFetch.mockReset();
  });

  it("normalizes the symbol and proxies the requested frequency without caching", async () => {
    mocks.radonFetch.mockResolvedValue(PAYLOAD);
    const { GET } = await import("../app/api/options/exposure/route");

    const response = await GET(
      new Request("http://localhost/api/options/exposure?symbol=mu&frequency=intraday"),
    );

    expect(response.status).toBe(200);
    expect((response.headers.get("Cache-Control") ?? "").toLowerCase()).toContain("no-store");
    expect(await response.json()).toEqual(PAYLOAD);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/options/exposure/MU?frequency=intraday",
      { timeout: 50_000 },
    );
  });

  it.each([
    ["http://localhost/api/options/exposure", "Required: symbol"],
    ["http://localhost/api/options/exposure?symbol=MU%24", "Invalid symbol"],
    ["http://localhost/api/options/exposure?symbol=MU&frequency=realtime", "Invalid frequency"],
  ])("rejects malformed requests before FastAPI: %s", async (url, expectedError) => {
    const { GET } = await import("../app/api/options/exposure/route");
    const response = await GET(new Request(url));

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: expectedError, code: "BAD_REQUEST" });
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("preserves status without exposing FastAPI authentication diagnostics", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValue(
      new RadonApiError(503, "Options exposure authentication is unavailable"),
    );
    const { GET } = await import("../app/api/options/exposure/route");

    const response = await GET(
      new Request("http://localhost/api/options/exposure?symbol=MU&frequency=eod"),
    );

    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body).toMatchObject({
      error: "Options exposure unavailable",
      detail: "Options service unavailable",
      code: "UPSTREAM_ERROR",
    });
    expect(JSON.stringify(body)).not.toContain("authentication");
  });

  it("maps local proxy timeouts to 504", async () => {
    mocks.radonFetch.mockRejectedValue(new Error("request timed out"));
    const { GET } = await import("../app/api/options/exposure/route");

    const response = await GET(
      new Request("http://localhost/api/options/exposure?symbol=MU&frequency=eod"),
    );

    expect(response.status).toBe(504);
    expect(await response.json()).toMatchObject({ code: "UPSTREAM_TIMEOUT" });
  });
});
