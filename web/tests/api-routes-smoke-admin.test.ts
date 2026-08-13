import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Smoke tests for /api/admin/* routes. One happy-path + one sad-path each.
 *
 * Each route is a thin proxy to FastAPI via radonFetch; the assertions
 * here protect the proxy/error-envelope contract, not the upstream
 * service's behavior. Companion file to `api-routes-smoke.test.ts`.
 */

// ---------------------------------------------------------------------------
// Mocks (mirrors api-routes-smoke.test.ts patterns)
// ---------------------------------------------------------------------------

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

// Demo-user management stays on requireDemoAdmin (DEMO_ADMIN_USER_IDS).
const mockRequireDemoAdmin = vi.fn();
vi.mock("@/lib/demo/adminAuth", () => ({
  requireDemoAdmin: mockRequireDemoAdmin,
}));

// Operator control-plane routes use requireRouteAccess({ operatorOnly: true }).
const mockRequireRouteAccess = vi.fn();
vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: mockRequireRouteAccess,
}));

function denyOperator(): { ok: false; response: Response } {
  return {
    ok: false,
    response: new Response(JSON.stringify({ error: { message: "Forbidden", status: 403 } }), {
      status: 403,
    }),
  };
}

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
  mockRequireDemoAdmin.mockReset();
  mockRequireDemoAdmin.mockResolvedValue("operator-user-id");
  mockRequireRouteAccess.mockReset();
  mockRequireRouteAccess.mockResolvedValue({
    ok: true,
    principal: { userId: "operator-user-id", kind: "operator" },
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

function req(url: string, init?: RequestInit): Request {
  return new Request(url, init);
}

async function jsonOf(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { __raw: text };
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("POST /api/admin/ib/restart", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, restarted: true });
    const { POST } = await import("../app/api/admin/ib/restart/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.ok).toBe(true);
  });

  it("returns 502 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream timeout"));
    const { POST } = await import("../app/api/admin/ib/restart/route");
    const res = await POST();
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });
});

describe("POST /api/admin/ib/reset-backoff", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, reset: true });
    const { POST } = await import("../app/api/admin/ib/reset-backoff/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 502 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("ECONNREFUSED"));
    const { POST } = await import("../app/api/admin/ib/reset-backoff/route");
    const res = await POST();
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });
});

describe("GET /api/admin/services", () => {
  it("returns 200 with service list on success", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      services: [{ unit: "radon-api.service", active: true }],
    });
    const { GET } = await import("../app/api/admin/services/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.services).toBeDefined();
  });

  it("returns 502 envelope when FastAPI fails", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { GET } = await import("../app/api/admin/services/route");
    const res = await GET();
    expect(res.status).toBe(502);
  });

  it("returns 403 and never reaches FastAPI when the operator gate denies", async () => {
    mockRequireRouteAccess.mockResolvedValueOnce(denyOperator());
    const { GET } = await import("../app/api/admin/services/route");
    const res = await GET();
    expect(res.status).toBe(403);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });
});

describe("POST /api/admin/services/[unit]/[action]", () => {
  it("returns 200 on valid unit/action when FastAPI succeeds", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, unit: "radon-api.service" });
    const { POST } = await import(
      "../app/api/admin/services/[unit]/[action]/route"
    );
    const res = await POST(
      req("http://localhost/api/admin/services/radon-api.service/restart") as never,
      { params: Promise.resolve({ unit: "radon-api.service", action: "restart" }) },
    );
    expect(res.status).toBe(200);
  });

  it("returns 400 when unit name is disallowed", async () => {
    const { POST } = await import(
      "../app/api/admin/services/[unit]/[action]/route"
    );
    const res = await POST(
      req("http://localhost/api/admin/services/evil-unit/restart") as never,
      { params: Promise.resolve({ unit: "evil-unit", action: "restart" }) },
    );
    expect(res.status).toBe(400);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });

  it("returns 400 when action is disallowed", async () => {
    const { POST } = await import(
      "../app/api/admin/services/[unit]/[action]/route"
    );
    const res = await POST(
      req("http://localhost/api/admin/services/radon-api.service/nuke") as never,
      { params: Promise.resolve({ unit: "radon-api.service", action: "nuke" }) },
    );
    expect(res.status).toBe(400);
  });

  it("returns 502 envelope when FastAPI fails", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("ECONNRESET"));
    const { POST } = await import(
      "../app/api/admin/services/[unit]/[action]/route"
    );
    const res = await POST(
      req("http://localhost/api/admin/services/radon-api.service/restart") as never,
      { params: Promise.resolve({ unit: "radon-api.service", action: "restart" }) },
    );
    expect(res.status).toBe(502);
  });
});

describe("POST /api/admin/stack/restart", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, restarted: ["radon-api"] });
    const { POST } = await import("../app/api/admin/stack/restart/route");
    const res = await POST(req("http://localhost/api/admin/stack/restart") as never);
    expect(res.status).toBe(200);
  });

  it("fails closed when the upstream connection drops before acceptance is proven", async () => {
    const abortErr = new Error("fetch failed: connection aborted");
    abortErr.name = "AbortError";
    mockRadonFetch.mockRejectedValueOnce(abortErr);
    const { POST } = await import("../app/api/admin/stack/restart/route");
    const res = await POST(req("http://localhost/api/admin/stack/restart") as never);
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });

  it("preserves a control-plane conflict instead of masking it as an accepted restart", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mockRadonFetch.mockRejectedValueOnce(new RadonApiError(409, "2FA push already in flight"));
    const { POST } = await import("../app/api/admin/stack/restart/route");
    const res = await POST(req("http://localhost/api/admin/stack/restart") as never);
    expect(res.status).toBe(409);
  });

  it("returns 502 envelope for non-drop FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("permission denied"));
    const { POST } = await import("../app/api/admin/stack/restart/route");
    const res = await POST(req("http://localhost/api/admin/stack/restart") as never);
    expect(res.status).toBe(502);
  });
});

// ---------------------------------------------------------------------------
// Fail-closed operator gate: when requireRouteAccess({ operatorOnly }) denies
// (ALLOWED_USER_IDS unset, or caller not the operator), the high-impact
// ops routes must 403 and NEVER reach the FastAPI proxy.
// ---------------------------------------------------------------------------
describe("admin ops routes fail closed when unauthorized", () => {
  beforeEach(() => {
    mockRequireRouteAccess.mockResolvedValue(denyOperator());
  });

  it("POST /api/admin/stack/restart → 403, no proxy call", async () => {
    const { POST } = await import("../app/api/admin/stack/restart/route");
    const res = await POST(req("http://localhost/api/admin/stack/restart") as never);
    expect(res.status).toBe(403);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("POST /api/admin/ib/restart → 403, no proxy call", async () => {
    const { POST } = await import("../app/api/admin/ib/restart/route");
    const res = await POST();
    expect(res.status).toBe(403);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("POST /api/admin/ib/reset-backoff → 403, no proxy call", async () => {
    const { POST } = await import("../app/api/admin/ib/reset-backoff/route");
    const res = await POST();
    expect(res.status).toBe(403);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("POST /api/admin/services/[unit]/[action] → 403 before unit validation", async () => {
    const { POST } = await import(
      "../app/api/admin/services/[unit]/[action]/route"
    );
    const res = await POST(
      req("http://localhost/api/admin/services/radon-api.service/restart") as never,
      { params: Promise.resolve({ unit: "radon-api.service", action: "restart" }) },
    );
    expect(res.status).toBe(403);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });
});
