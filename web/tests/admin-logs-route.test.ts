import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * GET /api/admin/logs: operator-only proxy to FastAPI /admin/logs. Mirrors the
 * /api/admin/services gate and envelope (api-routes-smoke-admin.test.ts).
 */

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

const mockRequireRouteAccess = vi.fn();
vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: mockRequireRouteAccess,
}));

function req(url: string): Request {
  return new Request(url);
}

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
  mockRequireRouteAccess.mockReset();
  mockRequireRouteAccess.mockResolvedValue({
    ok: true,
    principal: { userId: "operator-user-id", kind: "operator", token: "jwt" },
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("GET /api/admin/logs", () => {
  it("forwards ?service= to FastAPI /admin/logs", async () => {
    const body = { service: "dropbox-research", unit: "radon-dropbox-research.service", available: true, reason: null, entries: [], truncated: false };
    mockRadonFetch.mockResolvedValueOnce(body);
    const { GET } = await import("../app/api/admin/logs/route");
    const res = await GET(req("http://localhost/api/admin/logs?service=dropbox-research"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
    expect(mockRadonFetch).toHaveBeenCalledTimes(1);
    expect(mockRadonFetch.mock.calls[0][0]).toBe("/admin/logs?service=dropbox-research");
    expect(mockRadonFetch.mock.calls[0][1]).toMatchObject({ method: "GET" });
    expect(res.headers.get("cache-control")).toContain("no-store");
  });

  it("forwards ?unit= to FastAPI /admin/logs", async () => {
    mockRadonFetch.mockResolvedValueOnce({ available: true, entries: [] });
    const { GET } = await import("../app/api/admin/logs/route");
    await GET(req("http://localhost/api/admin/logs?unit=radon-cri-scan.service"));
    expect(mockRadonFetch.mock.calls[0][0]).toBe("/admin/logs?unit=radon-cri-scan.service");
  });

  it("drops unrelated query params", async () => {
    mockRadonFetch.mockResolvedValueOnce({ available: true, entries: [] });
    const { GET } = await import("../app/api/admin/logs/route");
    await GET(req("http://localhost/api/admin/logs?unit=radon-cri-scan.service&lines=99999"));
    expect(mockRadonFetch.mock.calls[0][0]).toBe("/admin/logs?unit=radon-cri-scan.service");
  });

  it("returns 400 without calling FastAPI when neither or both params are given", async () => {
    const { GET } = await import("../app/api/admin/logs/route");
    expect((await GET(req("http://localhost/api/admin/logs"))).status).toBe(400);
    expect((await GET(req("http://localhost/api/admin/logs?service=a&unit=radon-a.service"))).status).toBe(400);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("passes the upstream status through on a FastAPI error", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mockRadonFetch.mockRejectedValueOnce(new RadonApiError(400, "unknown unit"));
    const { GET } = await import("../app/api/admin/logs/route");
    const res = await GET(req("http://localhost/api/admin/logs?unit=radon-nope.service"));
    expect(res.status).toBe(400);
  });

  it("returns 502 envelope when FastAPI is unreachable", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { GET } = await import("../app/api/admin/logs/route");
    const res = await GET(req("http://localhost/api/admin/logs?service=dropbox-research"));
    expect(res.status).toBe(502);
  });

  it("returns 403 and never reaches FastAPI when the operator gate denies", async () => {
    mockRequireRouteAccess.mockResolvedValueOnce({
      ok: false,
      response: new Response(JSON.stringify({ error: { message: "Forbidden", status: 403 } }), { status: 403 }),
    });
    const { GET } = await import("../app/api/admin/logs/route");
    const res = await GET(req("http://localhost/api/admin/logs?service=dropbox-research"));
    expect(res.status).toBe(403);
    expect(mockRadonFetch).not.toHaveBeenCalled();
    expect(mockRequireRouteAccess).toHaveBeenCalledWith(undefined, { operatorOnly: true });
  });
});
