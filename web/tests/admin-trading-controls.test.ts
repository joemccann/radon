import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * REL-029 (R-053): operator-reachable kill switch.
 *
 * The FastAPI kill switch (/trading/halt, /trading/kill, /orders/cancel-all)
 * previously had NO browser-reachable client path. These tests pin the
 * /api/admin/trading/[action] proxy contract against a FAKE FastAPI (a
 * global-fetch stub that models the endpoint surface — never the real IB):
 *   - an authenticated operator fires kill and the fake observes the halt
 *     flag set + the mass-cancel invoked;
 *   - a non-allowlisted principal 403s before any upstream request;
 *   - destructive actions (kill, cancel-all) demand {confirm:true};
 *   - unknown actions 400 without touching upstream.
 */

// Handler-local authz seam. Default: allowlisted operator.
const mockRequireRouteAccess = vi.fn();
vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: mockRequireRouteAccess,
}));

type FakeFastApi = {
  halted: boolean;
  haltReason: string | null;
  cancelAllCalls: number;
  requests: { method: string; path: string; body: unknown }[];
};

function buildFakeFastApi(): FakeFastApi {
  return { halted: false, haltReason: null, cancelAllCalls: 0, requests: [] };
}

let fake: FakeFastApi;

/** Fake FastAPI: an in-memory halt flag + cancel counter behind fetch. */
function installFakeFastApi() {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const path = url.pathname;
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    fake.requests.push({ method, path, body });

    const json = (payload: unknown, status = 200) =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { "content-type": "application/json" },
      });

    if (method === "GET" && path === "/trading/status") {
      return json({ halted: fake.halted, reason: fake.haltReason });
    }
    if (method === "POST" && path === "/trading/halt") {
      fake.halted = true;
      fake.haltReason = body?.reason ?? "manual halt";
      return json({ halted: true, reason: fake.haltReason });
    }
    if (method === "POST" && path === "/trading/resume") {
      fake.halted = false;
      fake.haltReason = null;
      return json({ halted: false });
    }
    if (method === "POST" && path === "/orders/cancel-all") {
      if (body?.confirm !== true) {
        return json({ detail: { code: "CONFIRM_REQUIRED" } }, 400);
      }
      fake.cancelAllCalls += 1;
      return json({ status: "ok", cancelled: 2 });
    }
    if (method === "POST" && path === "/trading/kill") {
      // Models the FastAPI contract: halt FIRST, then mass-cancel.
      fake.halted = true;
      fake.haltReason = body?.reason ?? "kill switch";
      fake.cancelAllCalls += 1;
      return json({
        halted: true,
        halt: { halted: true, reason: fake.haltReason },
        cancel: { status: "ok", cancelled: 2 },
      });
    }
    return json({ detail: "not found" }, 404);
  });
}

function operatorAccess() {
  return {
    ok: true,
    principal: { userId: "operator-user-id", kind: "operator" },
  };
}

function deniedAccess() {
  return {
    ok: false,
    response: new Response(
      JSON.stringify({ error: { message: "Forbidden", status: 403 } }),
      { status: 403 },
    ),
  };
}

function req(action: string, body?: unknown, method = "POST"): [Request, { params: Promise<{ action: string }> }] {
  return [
    new Request(`http://localhost:3000/api/admin/trading/${action}`, {
      method,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    }),
    { params: Promise.resolve({ action }) },
  ];
}

async function loadRoute() {
  return import("../app/api/admin/trading/[action]/route");
}

beforeEach(() => {
  vi.resetModules();
  fake = buildFakeFastApi();
  installFakeFastApi();
  mockRequireRouteAccess.mockReset();
  mockRequireRouteAccess.mockResolvedValue(operatorAccess());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("POST /api/admin/trading/kill", () => {
  it("operator kill with confirm sets the halt flag and fires the mass-cancel on the fake FastAPI", async () => {
    const { POST } = await loadRoute();
    const res = await POST(...req("kill", { confirm: true, reason: "browser kill" }));
    expect(res.status).toBe(200);
    const payload = (await res.json()) as Record<string, unknown>;
    expect(payload.halted).toBe(true);
    expect(fake.halted).toBe(true);
    expect(fake.cancelAllCalls).toBe(1);
    expect(fake.requests).toEqual([
      { method: "POST", path: "/trading/kill", body: { reason: "browser kill" } },
    ]);
  });

  it("is operator-only: requires requireRouteAccess with operatorOnly", async () => {
    const { POST } = await loadRoute();
    await POST(...req("kill", { confirm: true }));
    expect(mockRequireRouteAccess).toHaveBeenCalledWith(
      undefined,
      expect.objectContaining({ operatorOnly: true }),
    );
  });

  it("403s a non-allowlisted principal before any upstream request", async () => {
    mockRequireRouteAccess.mockResolvedValue(deniedAccess());
    const { POST } = await loadRoute();
    const res = await POST(...req("kill", { confirm: true }));
    expect(res.status).toBe(403);
    expect(fake.requests).toHaveLength(0);
    expect(fake.halted).toBe(false);
  });

  it("400s without {confirm:true} and never reaches upstream", async () => {
    const { POST } = await loadRoute();
    const res = await POST(...req("kill", {}));
    expect(res.status).toBe(400);
    expect(fake.requests).toHaveLength(0);
    expect(fake.halted).toBe(false);
  });
});

describe("POST /api/admin/trading/cancel-all", () => {
  it("proxies to /orders/cancel-all with confirm:true", async () => {
    const { POST } = await loadRoute();
    const res = await POST(...req("cancel-all", { confirm: true }));
    expect(res.status).toBe(200);
    expect(fake.cancelAllCalls).toBe(1);
    expect(fake.requests[0]).toEqual({
      method: "POST",
      path: "/orders/cancel-all",
      body: { confirm: true },
    });
  });

  it("400s without {confirm:true} and never reaches upstream", async () => {
    const { POST } = await loadRoute();
    const res = await POST(...req("cancel-all"));
    expect(res.status).toBe(400);
    expect(fake.requests).toHaveLength(0);
  });
});

describe("POST /api/admin/trading/halt and resume", () => {
  it("halt forwards the reason and sets the fake halt flag", async () => {
    const { POST } = await loadRoute();
    const res = await POST(...req("halt", { reason: "operator pause" }));
    expect(res.status).toBe(200);
    expect(fake.halted).toBe(true);
    expect(fake.haltReason).toBe("operator pause");
  });

  it("resume clears the fake halt flag", async () => {
    fake.halted = true;
    const { POST } = await loadRoute();
    const res = await POST(...req("resume"));
    expect(res.status).toBe(200);
    expect(fake.halted).toBe(false);
  });
});

describe("GET /api/admin/trading/status", () => {
  it("returns the upstream halt state", async () => {
    fake.halted = true;
    fake.haltReason = "kill switch";
    const { GET } = await loadRoute();
    const res = await GET(...req("status", undefined, "GET"));
    expect(res.status).toBe(200);
    const payload = (await res.json()) as Record<string, unknown>;
    expect(payload.halted).toBe(true);
    expect(payload.reason).toBe("kill switch");
  });

  it("403s a non-allowlisted principal", async () => {
    mockRequireRouteAccess.mockResolvedValue(deniedAccess());
    const { GET } = await loadRoute();
    const res = await GET(...req("status", undefined, "GET"));
    expect(res.status).toBe(403);
    expect(fake.requests).toHaveLength(0);
  });
});

describe("unknown actions", () => {
  it("400s a POST to an unknown action without touching upstream", async () => {
    const { POST } = await loadRoute();
    const res = await POST(...req("detonate", { confirm: true }));
    expect(res.status).toBe(400);
    expect(fake.requests).toHaveLength(0);
  });

  it("400s a GET to a non-status action without touching upstream", async () => {
    const { GET } = await loadRoute();
    const res = await GET(...req("kill", undefined, "GET"));
    expect(res.status).toBe(400);
    expect(fake.requests).toHaveLength(0);
  });

  it("502 envelope when the upstream FastAPI is down", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new Error("ECONNREFUSED");
    });
    const { POST } = await loadRoute();
    const res = await POST(...req("halt", {}));
    expect(res.status).toBe(502);
    const payload = (await res.json()) as Record<string, unknown>;
    expect(payload.error).toBeDefined();
  });
});
