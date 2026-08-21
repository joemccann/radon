/**
 * @vitest-environment node
 *
 * /api/service-health is dual-auth (DUR-16): the loopback watchdog
 * authenticates with `Authorization: Bearer ${RADON_PROBE_FRESHNESS_TOKEN}`
 * and must be served WITHOUT a Clerk session. Regression: requireRouteAccess
 * was added to the route and 401'd the bearer-authenticated prober, which
 * disabled the Next.js DB-read auto-restart (nextjs-db-read error rows).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const PROBE_TOKEN = "probe-test-token";

function mockGetDb(rows: Record<string, unknown>[]): void {
  vi.doMock("@/lib/db", () => ({
    getDb: () => ({
      execute: vi.fn().mockResolvedValue({ rows }),
    }),
    resetDb: () => {},
  }));
}

function mockRouteAccessDenied(): void {
  vi.doMock("@/lib/routeAccess", () => ({
    requireRouteAccess: vi.fn().mockResolvedValue({
      ok: false,
      response: new Response(
        JSON.stringify({ error: "Unauthorized", code: "UNAUTHORIZED" }),
        { status: 401, headers: { "content-type": "application/json" } },
      ),
    }),
  }));
}

function probeRequest(token: string | null): Request {
  return new Request("http://127.0.0.1:3000/api/service-health", {
    headers: token == null ? {} : { authorization: `Bearer ${token}` },
  });
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllEnvs();
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("/api/service-health probe bearer", () => {
  it("serves a valid probe bearer without a Clerk session", async () => {
    vi.stubEnv("RADON_PROBE_FRESHNESS_TOKEN", PROBE_TOKEN);
    mockGetDb([]);
    mockRouteAccessDenied();
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET(probeRequest(PROBE_TOKEN));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.summary).toEqual({ total: 0, failing_count: 0 });
  });

  it("rejects a wrong bearer when route access denies", async () => {
    vi.stubEnv("RADON_PROBE_FRESHNESS_TOKEN", PROBE_TOKEN);
    mockGetDb([]);
    mockRouteAccessDenied();
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET(probeRequest("wrong-token"));
    expect(res.status).toBe(401);
  });

  it("falls through to route access when no bearer is presented", async () => {
    vi.stubEnv("RADON_PROBE_FRESHNESS_TOKEN", PROBE_TOKEN);
    mockGetDb([]);
    mockRouteAccessDenied();
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET(probeRequest(null));
    expect(res.status).toBe(401);
  });

  it("never authorizes the bearer path when the server token is unset", async () => {
    mockGetDb([]);
    mockRouteAccessDenied();
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET(probeRequest(PROBE_TOKEN));
    expect(res.status).toBe(401);
  });
});
