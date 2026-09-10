/**
 * @vitest-environment node
 *
 * REL-232 / R-639: /api/orders/modify has no server-side floor on
 * newQuantity against the already-filled count. A total below `filled`
 * means IB fill-and-cancel: the remainder silently shrinks to zero.
 *
 * The route must refuse (422) before anything is forwarded upstream.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const SNAPSHOT_ORDER = {
  orderId: 7,
  permId: 7007,
  symbol: "VIX",
  contract: {
    conId: 71,
    symbol: "VIX",
    secType: "OPT",
    strike: 30,
    right: "C",
    expiry: "20261020",
  },
  action: "BUY",
  orderType: "LMT",
  totalQuantity: 1000,
  limitPrice: 0.61,
  auxPrice: null,
  status: "Submitted",
  filled: 16,
  remaining: 984,
  avgFillPrice: 0.61,
  tif: "DAY",
};

vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  EMPTY_ORDERS: {
    open_orders: [],
    executed_orders: [],
    timestamp: "",
  },
  readOrdersSnapshotFromDb: vi.fn(async () => ({
    open_orders: [SNAPSHOT_ORDER],
    executed_orders: [],
    timestamp: "2026-09-05T14:00:00.000Z",
  })),
}));

function recordUpstream() {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      calls.push({ url, method: init?.method ?? "GET", body });
      return new Response(JSON.stringify({ message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

function modifyRequest(body: Record<string, unknown>): Request {
  return new Request("http://localhost/api/orders/modify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("POST /api/orders/modify floors newQuantity at the filled count", () => {
  it("refuses (422) a total below the snapshot's filled count, forwarding nothing", async () => {
    const upstream = recordUpstream();
    const { POST } = await import("../app/api/orders/modify/route");

    // 16 already filled; a total of 10 would be IB fill-and-cancel.
    const res = await POST(modifyRequest({ permId: 7007, newQuantity: 10 }));

    expect(res.status).toBe(422);
    const body = (await res.json()) as { error: string };
    expect(body.error).toMatch(/16/);
    expect(upstream).toHaveLength(0);
  });

  it("forwards a total at or above the filled count", async () => {
    const upstream = recordUpstream();
    const { POST } = await import("../app/api/orders/modify/route");

    const res = await POST(modifyRequest({ permId: 7007, newQuantity: 516 }));

    // Not refused by the floor (the confirm step may still dispute it, but
    // the upstream modify must have been attempted).
    expect(res.status).not.toBe(422);
    const modifyCall = upstream.find((call) => call.url.endsWith("/orders/modify"));
    expect(modifyCall?.method).toBe("POST");
    expect((modifyCall?.body as Record<string, unknown>).newQuantity).toBe(516);
  });
});
