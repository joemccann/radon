import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Every GET /api/admin/health 502 in 48h of prod logs landed inside a
 * radon-api restart window: Next serves for ~7s before uvicorn accepts, and
 * the proxy mapped the refused socket to 502. FastAPI restarting is an
 * expected, recurring state, so it is a 200 degraded verdict
 * (missing: true), not a 5xx. Real HTTP errors from FastAPI keep their status.
 */

const RADON_API = "http://localhost:8321";
vi.stubEnv("RADON_API_URL", RADON_API);

const mockFetch = vi.fn();
let timeoutSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.resetModules();
  mockFetch.mockReset();
  vi.stubGlobal("fetch", mockFetch);
  timeoutSpy = vi.spyOn(AbortSignal, "timeout");
});

afterEach(() => {
  timeoutSpy.mockRestore();
  vi.unstubAllGlobals();
});

function refused(): TypeError {
  return new TypeError("fetch failed", { cause: Object.assign(new Error("connect ECONNREFUSED"), { code: "ECONNREFUSED" }) });
}

async function getHealth() {
  const { GET } = await import("../app/api/admin/health/route");
  const response = await GET();
  return { response, body: (await response.json()) as Record<string, unknown> };
}

describe("GET /api/admin/health upstream failures", () => {
  it("probes FastAPI /health with a deadline below the dashboard's own abort", async () => {
    const { ADMIN_HEALTH_UPSTREAM_TIMEOUT_MS } = await import("../lib/edgeBudget");
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ status: "ok", ib_gateway: {}, ib_pool: {} }), { status: 200 }));

    const { response } = await getHealth();

    expect(response.status).toBe(200);
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${RADON_API}/health`);
    expect(init.method).toBe("GET");
    expect(timeoutSpy).toHaveBeenCalledWith(ADMIN_HEALTH_UPSTREAM_TIMEOUT_MS);
  });

  it("reports a refused connection as a 200 unreachable verdict", async () => {
    mockFetch.mockRejectedValue(refused());

    const { response, body } = await getHealth();

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toContain("no-store");
    expect(body).toMatchObject({ status: "unreachable", missing: true, api_reachable: false, reason: "unreachable" });
    expect(body.ib_gateway).toBeUndefined();
  });

  it("reports an elapsed deadline as a 200 timeout verdict", async () => {
    mockFetch.mockRejectedValue(new DOMException("The operation was aborted due to timeout", "TimeoutError"));

    const { response, body } = await getHealth();

    expect(response.status).toBe(200);
    expect(body).toMatchObject({ missing: true, reason: "timeout" });
  });

  it("passes a real FastAPI HTTP error through with its status", async () => {
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "forbidden" }), { status: 403 }));

    const { response } = await getHealth();

    expect(response.status).toBe(403);
  });
});
