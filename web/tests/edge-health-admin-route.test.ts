/**
 * @vitest-environment node
 *
 * F10 round 2 — the admin proxy must reach the health daemon as a TRUSTED
 * caller.
 *
 * /status is now trust-split: anything arriving through the public edge gets
 * the aggregate verdict only, with no `units`, `probes` or `service_health`
 * sections. This route feeds exactly those two admin reliability sections (the
 * unit inventory and the WriterFreshnessTable rows), so a default of
 * https://app.radon.run/edge-health/status makes it leave the box, re-enter
 * through Caddy, and come back redacted — the panel renders empty and reads as
 * healthy. Two controls are pinned here:
 *
 *   1. the daemon is read over LOOPBACK first (127.0.0.1:8330/status, no proxy
 *      headers => full detail), with the public edge kept only as the fallback
 *      for laptop dev where there is no local daemon;
 *   2. when RADON_HEALTH_STATUS_TOKEN is configured the request carries the
 *      server-side bearer, so detail survives even on the edge path.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const DETAILED_BODY = {
  schema_version: 2,
  ok: false,
  overall_state: "degraded",
  probes: { "radon-api": { state: "up" } },
  units: { "radon-api.service": { state: "up" } },
  service_health: { state: "ok", rows: [] },
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.resetModules();
  delete process.env.RADON_EDGE_HEALTH_URL;
  delete process.env.RADON_HEALTH_STATUS_TOKEN;
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete process.env.RADON_EDGE_HEALTH_URL;
  delete process.env.RADON_HEALTH_STATUS_TOKEN;
});

describe("GET /api/admin/edge-health", () => {
  it("reads the daemon over loopback, not the public edge", async () => {
    fetchMock.mockResolvedValue(jsonResponse(DETAILED_BODY));

    const { GET } = await import("../app/api/admin/edge-health/route");
    const res = await GET();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://127.0.0.1:8330/status");
    await expect(res.json()).resolves.toMatchObject({
      reachable: true,
      units: { "radon-api.service": { state: "up" } },
    });
  });

  it("carries the server-side bearer token when one is configured", async () => {
    process.env.RADON_HEALTH_STATUS_TOKEN = "s3cret-status-token";
    fetchMock.mockResolvedValue(jsonResponse(DETAILED_BODY));

    const { GET } = await import("../app/api/admin/edge-health/route");
    await GET();

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer s3cret-status-token");
  });

  it("sends no Authorization header when no token is configured", async () => {
    fetchMock.mockResolvedValue(jsonResponse(DETAILED_BODY));

    const { GET } = await import("../app/api/admin/edge-health/route");
    await GET();

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBeNull();
  });

  it("falls back to the public edge when there is no local daemon (laptop dev)", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("connect ECONNREFUSED 127.0.0.1:8330"))
      .mockResolvedValueOnce(jsonResponse(DETAILED_BODY));

    const { GET } = await import("../app/api/admin/edge-health/route");
    const res = await GET();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[1][0])).toBe("https://app.radon.run/edge-health/status");
    await expect(res.json()).resolves.toMatchObject({ reachable: true });
  });

  it("still reports unreachable as a payload flag when every source fails", async () => {
    fetchMock.mockRejectedValue(new Error("boom"));

    const { GET } = await import("../app/api/admin/edge-health/route");
    const res = await GET();

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({ reachable: false });
  });

  it("honours an explicit RADON_EDGE_HEALTH_URL override", async () => {
    process.env.RADON_EDGE_HEALTH_URL = "http://health.internal:8330/status";
    fetchMock.mockResolvedValue(jsonResponse(DETAILED_BODY));

    const { GET } = await import("../app/api/admin/edge-health/route");
    await GET();

    expect(String(fetchMock.mock.calls[0][0])).toBe("http://health.internal:8330/status");
  });
});
