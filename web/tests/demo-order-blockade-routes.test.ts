import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * T-018 — the demo order blockade is a PERIMETER, not a place-route detail.
 *
 * Two regressions are pinned here at the ROUTE level (the unit pins live in
 * demo-order-blockade.test.ts):
 *
 *   A. Fail-closed. `resolveDemoOrderDecision` used to return `{action:"allow"}`
 *      whenever Clerk threw, so a single auth hiccup dropped a demo user onto
 *      the REAL IB path. In production an unresolvable cohort must now refuse
 *      the order (503), never place it.
 *
 *   B. Coverage. /api/orders/modify and /api/orders/cancel never consulted the
 *      blockade at all — and modify's `replaceOrder` branch performs a REAL
 *      /orders/cancel + /orders/place pair, i.e. a demo user could place a live
 *      combo through the modify route. Both routes are now gated.
 *
 * The assertion that matters in every case: radonFetch is NEVER called with a
 * live order path.
 */

const mockRadonFetch = vi.fn();
const mockAuth = vi.fn();
const mockReadOrders = vi.fn();

const EMPTY = {
  last_sync: "",
  open_orders: [] as Array<Record<string, unknown>>,
  executed_orders: [] as Array<Record<string, unknown>>,
  open_count: 0,
  executed_count: 0,
};

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: class extends Error {
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

vi.mock("@clerk/nextjs/server", () => ({ auth: mockAuth }));

vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  EMPTY_ORDERS: EMPTY,
  readOrdersSnapshotFromDb: mockReadOrders,
}));

const DAY_MS = 86_400_000;

// Window-relative, never hardcoded: a pinned calendar date rots the moment CI
// runs past it.
const activeDemoClaims = () => ({
  userId: "user_demo",
  sessionClaims: {
    metadata: {
      demoRole: "trial",
      demoTrialExpiresAt: new Date(Date.now() + 7 * DAY_MS).toISOString(),
    },
  },
});

const expiredDemoClaims = () => ({
  userId: "user_demo",
  sessionClaims: {
    metadata: {
      demoRole: "trial",
      demoTrialExpiresAt: new Date(Date.now() - DAY_MS).toISOString(),
    },
  },
});

const operatorClaims = () => ({ userId: "user_operator", sessionClaims: { metadata: null } });

/** Clerk down / no request context. */
const clerkThrows = () => {
  mockAuth.mockRejectedValue(new Error("clerkMiddleware() was not run"));
  vi.stubEnv("NODE_ENV", "production");
};

const LIVE_ORDER_PATHS = ["/orders/place", "/orders/modify", "/orders/cancel"];

function upstreamCalls(): string[] {
  return mockRadonFetch.mock.calls.map((call) => String(call[0]));
}

function expectNoLiveOrderCall(): void {
  const calls = upstreamCalls();
  for (const path of LIVE_ORDER_PATHS) {
    expect(calls, `${path} must not be reached`).not.toContain(path);
  }
}

function post(path: string, body: Record<string, unknown>): Request {
  return new Request(`http://localhost${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const MODIFY_BODY = { orderId: 55, newPrice: 2.5 };
const CANCEL_BODY = { orderId: 55 };
const REPLACE_BODY = {
  orderId: 55,
  cancelOrders: [{ orderId: 55 }],
  replaceOrder: {
    type: "combo",
    symbol: "SPY",
    action: "BUY",
    quantity: 1,
    limitPrice: -1.25,
    legs: [
      { expiry: "20260918", strike: 500, right: "C", action: "BUY", ratio: 1 },
      { expiry: "20260918", strike: 520, right: "C", action: "SELL", ratio: 1 },
    ],
  },
};
const PLACE_BODY = {
  type: "stock",
  symbol: "PLTR",
  action: "BUY",
  quantity: 1,
  limitPrice: 150,
};

const CONFIRMED_MODIFY_SNAPSHOT = {
  ...EMPTY,
  open_orders: [{ orderId: 55, permId: 0, limitPrice: 2.5, totalQuantity: 10 }],
};

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
  mockAuth.mockReset();
  mockReadOrders.mockReset();
  mockRadonFetch.mockResolvedValue({ message: "ok", orderId: 1, permId: 2, initialStatus: "Submitted" });
  mockReadOrders.mockResolvedValue(EMPTY);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("POST /api/orders/modify — demo blockade (T-018 B)", () => {
  it("active demo: refuses with 403 and never calls /orders/modify", async () => {
    mockAuth.mockResolvedValue(activeDemoClaims());
    const { POST } = await import("../app/api/orders/modify/route");

    const res = await POST(post("/api/orders/modify", MODIFY_BODY));

    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("FORBIDDEN");
    expectNoLiveOrderCall();
  });

  it("active demo + replaceOrder: no REAL cancel + place pair escapes", async () => {
    mockAuth.mockResolvedValue(activeDemoClaims());
    const { POST } = await import("../app/api/orders/modify/route");

    const res = await POST(post("/api/orders/modify", REPLACE_BODY));

    expect(res.status).toBe(403);
    // The pre-fix path did radonFetch("/orders/cancel") then
    // radonFetch("/orders/place") — a live combo placed by a demo user.
    expectNoLiveOrderCall();
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("expired demo: 403, no live call", async () => {
    mockAuth.mockResolvedValue(expiredDemoClaims());
    const { POST } = await import("../app/api/orders/modify/route");

    const res = await POST(post("/api/orders/modify", MODIFY_BODY));

    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("FORBIDDEN");
    expectNoLiveOrderCall();
  });

  it("Clerk unavailable in production: 503, no live call", async () => {
    clerkThrows();
    const { POST } = await import("../app/api/orders/modify/route");

    const res = await POST(post("/api/orders/modify", MODIFY_BODY));

    expect(res.status).toBe(401);
    expect((await res.json()).code).toBe("UNAUTHORIZED");
    expectNoLiveOrderCall();
  });

  it("non-demo operator: unaffected, still forwards to /orders/modify", async () => {
    mockAuth.mockResolvedValue(operatorClaims());
    vi.stubEnv("ALLOWED_USER_IDS", "user_operator");
    mockReadOrders.mockResolvedValue(CONFIRMED_MODIFY_SNAPSHOT);
    const { POST } = await import("../app/api/orders/modify/route");

    const res = await POST(post("/api/orders/modify", MODIFY_BODY));

    expect(res.status).toBe(200);
    expect(upstreamCalls()).toContain("/orders/modify");
  });
});

describe("POST /api/orders/cancel — demo blockade (T-018 B)", () => {
  it("active demo: 403 and never calls /orders/cancel", async () => {
    mockAuth.mockResolvedValue(activeDemoClaims());
    const { POST } = await import("../app/api/orders/cancel/route");

    const res = await POST(post("/api/orders/cancel", CANCEL_BODY));

    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("FORBIDDEN");
    expectNoLiveOrderCall();
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("expired demo: 403, no live call", async () => {
    mockAuth.mockResolvedValue(expiredDemoClaims());
    const { POST } = await import("../app/api/orders/cancel/route");

    const res = await POST(post("/api/orders/cancel", CANCEL_BODY));

    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("FORBIDDEN");
    expectNoLiveOrderCall();
  });

  it("Clerk unavailable in production: 503, no live call", async () => {
    clerkThrows();
    const { POST } = await import("../app/api/orders/cancel/route");

    const res = await POST(post("/api/orders/cancel", CANCEL_BODY));

    expect(res.status).toBe(401);
    expect((await res.json()).code).toBe("UNAUTHORIZED");
    expectNoLiveOrderCall();
  });

  it("non-demo operator: unaffected, still forwards to /orders/cancel", async () => {
    mockAuth.mockResolvedValue(operatorClaims());
    vi.stubEnv("ALLOWED_USER_IDS", "user_operator");
    const { POST } = await import("../app/api/orders/cancel/route");

    const res = await POST(post("/api/orders/cancel", CANCEL_BODY));

    expect(res.status).toBe(200);
    expect(upstreamCalls()).toContain("/orders/cancel");
  });
});

describe("POST /api/orders/place — fail-closed (T-018 A)", () => {
  it("active demo cannot enter the live placement route", async () => {
    mockAuth.mockResolvedValue(activeDemoClaims());
    const { POST } = await import("../app/api/orders/place/route");

    const res = await POST(post("/api/orders/place", PLACE_BODY));

    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("FORBIDDEN");
    expect(upstreamCalls()).not.toContain("/paper/place");
    expectNoLiveOrderCall();
  });

  it("Clerk unavailable in production: 503, order NOT placed and NOT papered", async () => {
    clerkThrows();
    const { POST } = await import("../app/api/orders/place/route");

    const res = await POST(post("/api/orders/place", PLACE_BODY));

    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body.code).toBe("UNAUTHORIZED");
    expectNoLiveOrderCall();
    expect(upstreamCalls()).not.toContain("/paper/place");
  });

  it("outside production an unresolvable cohort still allows the real path", async () => {
    // Deliberate carve-out: vitest and `next dev` have no Clerk request
    // context, so a throw there means "no Clerk", not "unknown user". Pinned so
    // the carve-out can never silently widen to production.
    mockAuth.mockRejectedValue(new Error("clerkMiddleware() was not run"));
    const { POST } = await import("../app/api/orders/place/route");

    const res = await POST(post("/api/orders/place", PLACE_BODY));

    expect(res.status).toBe(200);
    expect(upstreamCalls()).toContain("/orders/place");
  });
});
