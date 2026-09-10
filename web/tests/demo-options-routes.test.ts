import { beforeEach, describe, expect, it, vi } from "vitest";

import { isOptionsExposurePayload } from "@/lib/optionsExposure";

const mocks = vi.hoisted(() => ({
  principalKind: "demo" as "demo" | "operator",
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: vi.fn(async () => ({
    ok: true,
    principal: { userId: "demo-user", kind: mocks.principalKind },
  })),
}));

vi.mock("@/lib/radonApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/radonApi")>();
  return { ...actual, radonFetch: mocks.radonFetch };
});

describe("demo option routes", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.principalKind = "demo";
    mocks.radonFetch.mockReset();
    mocks.radonFetch.mockRejectedValue(new Error("FastAPI must not run for demo fixtures"));
  });

  it("serves expirations and a selected chain without FastAPI", async () => {
    const { GET: getExpirations } = await import("../app/api/options/expirations/route");
    const expirationResponse = await getExpirations(
      new Request("http://localhost/api/options/expirations?symbol=amat"),
    );
    const expirations = await expirationResponse.json() as {
      symbol: string;
      expirations: string[];
    };

    expect(expirationResponse.status).toBe(200);
    expect(expirationResponse.headers.get("Cache-Control")).toContain("no-store");
    expect(expirations.symbol).toBe("AMAT");

    const expiry = expirations.expirations.at(-1)!;
    const { GET: getChain } = await import("../app/api/options/chain/route");
    const chainResponse = await getChain(
      new Request(`http://localhost/api/options/chain?symbol=AMAT&expiry=${expiry}`),
    );
    const chain = await chainResponse.json() as { strikes: number[] };

    expect(chainResponse.status).toBe(200);
    expect(chainResponse.headers.get("Cache-Control")).toContain("no-store");
    expect(chain.strikes).toContain(400);
    expect(chain.strikes).toContain(485);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("serves normalized options exposure without FastAPI", async () => {
    const { GET } = await import("../app/api/options/exposure/route");
    const response = await GET(
      new Request("http://localhost/api/options/exposure?symbol=aapl&frequency=intraday"),
    );
    const payload: unknown = await response.json();

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toContain("no-store");
    expect(isOptionsExposurePayload(payload)).toBe(true);
    expect(payload).toMatchObject({ symbol: "AAPL", frequency: "intraday" });
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("preserves the non-demo proxy path", async () => {
    mocks.principalKind = "operator";
    mocks.radonFetch.mockResolvedValue({ symbol: "AAPL", expirations: ["20261016"] });
    const { GET } = await import("../app/api/options/expirations/route");
    const response = await GET(
      new Request("http://localhost/api/options/expirations?symbol=AAPL"),
    );

    expect(response.status).toBe(200);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/options/expirations?symbol=AAPL",
      { timeout: 50_000 },
    );
  });
});
