/**
 * @vitest-environment node
 *
 * TEST_AUDIT T-128 — R-179 / R-180 / R-186 exercised as BEHAVIOUR.
 *
 * `auth-perimeter-delta.test.ts` pinned these fixes with regexes over route
 * SOURCE (`toContain("requireRouteAccess")`, `toMatch(/rate:\s*\{/)`,
 * `toMatch(/probeView|probePayload|forProbe/)`). Deleting the guard CALL and
 * keeping the import, writing `token: undefined,`, or leaving `forProbe`
 * defined but unused all stayed green. These tests import the handlers and
 * assert what a caller actually gets.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
  requireRouteAccess: vi.fn(),
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mocks.radonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: mocks.requireRouteAccess,
}));

const denied = () => ({
  ok: false,
  response: new Response(JSON.stringify({ error: "Unauthorized" }), { status: 401 }),
});
const allowed = (token = "clerk-session-jwt") => ({
  ok: true,
  principal: { kind: "operator", userId: "user_operator", token },
});

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllEnvs();
  mocks.radonFetch.mockReset();
  mocks.requireRouteAccess.mockReset();
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("R-179: POST /api/ib/ws-ticket", () => {
  it("returns 401 and never mints when route access denies", async () => {
    mocks.requireRouteAccess.mockResolvedValueOnce(denied());
    const { POST } = await import("../app/api/ib/ws-ticket/route");
    const res = await POST(new Request("http://localhost/api/ib/ws-ticket", { method: "POST" }));
    expect(res.status).toBe(401);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("mints with the authenticated principal's OWN token, never an omitted header", async () => {
    mocks.requireRouteAccess.mockResolvedValueOnce(allowed("clerk-session-jwt"));
    mocks.radonFetch.mockResolvedValueOnce({ ticket: "t-1" });
    const { POST } = await import("../app/api/ib/ws-ticket/route");
    const res = await POST(new Request("http://localhost/api/ib/ws-ticket", { method: "POST" }));
    expect(res.status).toBe(200);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    const [, options] = mocks.radonFetch.mock.calls[0] as [string, { token?: string }];
    expect(options.token).toBe("clerk-session-jwt");
  });

  it("asks the guard for a per-user rate ceiling", async () => {
    mocks.requireRouteAccess.mockResolvedValueOnce(denied());
    const { POST } = await import("../app/api/ib/ws-ticket/route");
    await POST(new Request("http://localhost/api/ib/ws-ticket", { method: "POST" }));
    const [, options] = mocks.requireRouteAccess.mock.calls[0] as [unknown, { rate?: { limit: number; windowMs: number } }];
    expect(options.rate?.limit).toBeGreaterThan(0);
    expect(options.rate?.windowMs).toBeGreaterThan(0);
  });
});

describe("R-180: POST /api/garch-convergence/scan", () => {
  const req = () =>
    new Request("http://localhost/api/garch-convergence/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "largecaps" }),
    });

  it("returns 401 and spawns nothing when route access denies", async () => {
    mocks.requireRouteAccess.mockResolvedValueOnce(denied());
    const { POST } = await import("../app/api/garch-convergence/scan/route");
    const res = await POST(req());
    expect(res.status).toBe(401);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("forwards the scan once admitted, under a per-user rate ceiling", async () => {
    mocks.requireRouteAccess.mockResolvedValueOnce(allowed());
    mocks.radonFetch.mockResolvedValueOnce({ status: "started" });
    const { POST } = await import("../app/api/garch-convergence/scan/route");
    const res = await POST(req());
    expect(res.status).toBe(200);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/garch-convergence/scan?preset=largecaps",
      expect.objectContaining({ method: "POST" }),
    );
    const [, options] = mocks.requireRouteAccess.mock.calls[0] as [unknown, { rate?: { limit: number } }];
    expect(options.rate?.limit).toBeGreaterThan(0);
  });
});

describe("R-186: GET /api/service-health probe bearer", () => {
  const PROBE_TOKEN = "probe-test-token";
  const ROW = {
    service: "journal_sync",
    state: "error",
    last_error: "Flex 1025: token throttled",
    updated_at: "2026-08-25T00:00:00Z",
  };

  function mockDbRows(rows: Record<string, unknown>[]): void {
    vi.doMock("@/lib/db", () => ({
      getDb: () => ({ execute: vi.fn().mockResolvedValue({ rows }) }),
      resetDb: () => {},
    }));
  }

  const probe = () =>
    new Request("http://127.0.0.1:3000/api/service-health", {
      headers: { authorization: `Bearer ${PROBE_TOKEN}`, "x-forwarded-for": "10.0.0.7" },
    });

  it("serves the probe states and freshness but no writer diagnostics", async () => {
    vi.stubEnv("RADON_PROBE_FRESHNESS_TOKEN", PROBE_TOKEN);
    mockDbRows([ROW]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET(probe());
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.services).toHaveLength(1);
    expect(body.services[0].service).toBe("journal_sync");
    expect(body.services[0].state).toBe("error");
    expect(body.services[0].last_error).toBeNull();
    expect(body.services[0].error_summary).toBeNull();
    expect(body.failing[0].last_error).toBeNull();
    expect(mocks.requireRouteAccess).not.toHaveBeenCalled();
  });

  it("serves an operator session the full diagnostic payload", async () => {
    mockDbRows([ROW]);
    mocks.requireRouteAccess.mockResolvedValueOnce(allowed());
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET(new Request("http://127.0.0.1:3000/api/service-health"));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.services[0].last_error).toContain("1025");
    expect(typeof body.services[0].error_summary).toBe("string");
  });

  it("429s the shared static bearer past its per-minute ceiling", async () => {
    vi.stubEnv("RADON_PROBE_FRESHNESS_TOKEN", PROBE_TOKEN);
    mockDbRows([]);
    const { GET } = await import("../app/api/service-health/route");
    let first429: Response | null = null;
    for (let i = 0; i < 200; i += 1) {
      const res = await GET(probe());
      if (res.status === 429) {
        first429 = res;
        expect(i).toBeGreaterThanOrEqual(30);
        break;
      }
      expect(res.status).toBe(200);
    }
    expect(first429).not.toBeNull();
    expect(Number(first429!.headers.get("Retry-After"))).toBeGreaterThan(0);
  });
});
