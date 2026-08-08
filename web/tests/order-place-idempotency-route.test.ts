import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Integration: POST /api/orders/place must not place the same real-money order
 * twice on a double-click / client retry (2026-06-29 audit follow-up). The route
 * dedups by content hash (default) or an explicit client idempotencyKey.
 */

const mockRadonFetch = vi.fn();
const mockReadDataFile = vi.fn();

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

vi.mock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));
vi.mock("@tools/schemas/ib-orders", () => ({ OrdersData: {} }));

function placeReq(body: Record<string, unknown>): Request {
  return new Request("http://localhost/api/orders/place", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const STOCK = { type: "stock", symbol: "PLTR", action: "BUY", quantity: 100, limitPrice: 150 };

function placeCallCount(): number {
  return mockRadonFetch.mock.calls.filter((c) => c[0] === "/orders/place").length;
}

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
  mockReadDataFile.mockReset();
  mockReadDataFile.mockResolvedValue({ ok: true, data: { positions: [] } });
  // /orders/place succeeds; /orders/refresh + anything else resolve trivially.
  mockRadonFetch.mockImplementation((url: string) =>
    url === "/orders/place"
      ? Promise.resolve({ orderId: 1, permId: 2, initialStatus: "Submitted", message: "ok" })
      : Promise.resolve({}),
  );
});

describe("POST /api/orders/place idempotency", () => {
  it("dedups an immediate identical resubmit — places once, flags the duplicate", async () => {
    const { POST } = await import("../app/api/orders/place/route");

    const r1 = await POST(placeReq(STOCK));
    const r2 = await POST(placeReq(STOCK));

    expect(r1.status).toBe(200);
    expect(r2.status).toBe(200);
    expect(placeCallCount()).toBe(1); // the order reached IB exactly once
    const b1 = await r1.json();
    const b2 = await r2.json();
    expect(b1.deduplicated).toBeUndefined();
    expect(b2.deduplicated).toBe(true);
    expect(b2.orderId).toBe(1); // duplicate still gets the real result
  });

  it("does NOT dedup genuinely different orders", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    await POST(placeReq(STOCK));
    await POST(placeReq({ ...STOCK, quantity: 200 })); // different intent
    expect(placeCallCount()).toBe(2);
  });

  it("dedups on a reused explicit idempotencyKey even across content", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    await POST(placeReq({ ...STOCK, idempotencyKey: "intent-abc" }));
    // Same key = same user intent (a retry) → deduped, not placed again.
    const r2 = await POST(placeReq({ ...STOCK, idempotencyKey: "intent-abc" }));
    expect(placeCallCount()).toBe(1);
    expect((await r2.json()).deduplicated).toBe(true);
  });

  it("distinct explicit keys place distinct orders", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    await POST(placeReq({ ...STOCK, idempotencyKey: "intent-1" }));
    await POST(placeReq({ ...STOCK, idempotencyKey: "intent-2" }));
    expect(placeCallCount()).toBe(2);
  });

  it("an IB rejection is NOT cached — a retry re-attempts", async () => {
    mockRadonFetch.mockReset();
    mockRadonFetch.mockImplementation((url: string) => {
      if (url === "/orders/place") {
        return Promise.resolve({ orderId: 9, initialStatus: "Cancelled" }); // silent reject
      }
      return Promise.resolve({});
    });
    const { POST } = await import("../app/api/orders/place/route");

    const r1 = await POST(placeReq(STOCK));
    expect(r1.status).toBe(502);
    const r2 = await POST(placeReq(STOCK)); // retry after rejection
    expect(r2.status).toBe(502);
    expect(placeCallCount()).toBe(2); // re-attempted, not deduped to the rejection
  });
});

/**
 * T-021 — the ambiguity window. Next.js aborts the upstream placement at 30s
 * while FastAPI's script budget is 25s, so a `TimeoutError` can surface with the
 * order already live at IB. That must never be answered with the generic 500
 * INTERNAL_ERROR envelope: it reads as "nothing happened", and the operator
 * retry it invites double-places a real-money order.
 */
describe("POST /api/orders/place upstream timeout is indeterminate, not a generic 500", () => {
  const placementTimeout = () =>
    new DOMException("The operation was aborted due to timeout", "TimeoutError");

  function timeoutOnPlace(): void {
    mockRadonFetch.mockReset();
    mockRadonFetch.mockImplementation((url: string) =>
      url === "/orders/place" ? Promise.reject(placementTimeout()) : Promise.resolve({}),
    );
  }

  it("returns 504 with the indeterminate code and the check-open-orders instruction", async () => {
    timeoutOnPlace();
    const { POST } = await import("../app/api/orders/place/route");

    const res = await POST(placeReq(STOCK));

    expect(res.status).toBe(504);
    const body = await res.json();
    expect(body.code).toBe("UPSTREAM_TIMEOUT_ORDER_INDETERMINATE");
    expect(body.code).not.toBe("INTERNAL_ERROR");
    expect(body.error).toMatch(/may have reached IB/i);
    expect(body.error).toMatch(/check open orders before re-placing/i);
    expect(placeCallCount()).toBe(1);
  });

  it("does NOT re-place an identical resubmit inside the ambiguity window", async () => {
    timeoutOnPlace();
    const { POST } = await import("../app/api/orders/place/route");

    const r1 = await POST(placeReq(STOCK));
    const r2 = await POST(placeReq(STOCK)); // operator hits submit again

    expect(r1.status).toBe(504);
    expect(r2.status).toBe(504);
    expect(placeCallCount()).toBe(1); // the order never reached IB twice
    const b2 = await r2.json();
    expect(b2.code).toBe("UPSTREAM_TIMEOUT_ORDER_INDETERMINATE");
    expect(b2.detail).toMatch(/NOT sent again/i);
  });

  it("holds a reused explicit idempotencyKey to the same indeterminate answer", async () => {
    timeoutOnPlace();
    const { POST } = await import("../app/api/orders/place/route");

    await POST(placeReq({ ...STOCK, idempotencyKey: "intent-timeout" }));
    const r2 = await POST(placeReq({ ...STOCK, idempotencyKey: "intent-timeout" }));

    expect(r2.status).toBe(504);
    expect(placeCallCount()).toBe(1);
  });

  it("scopes the hold to that order — a different order still places", async () => {
    mockRadonFetch.mockReset();
    let placeCalls = 0;
    mockRadonFetch.mockImplementation((url: string) => {
      if (url !== "/orders/place") return Promise.resolve({});
      placeCalls += 1;
      return placeCalls === 1
        ? Promise.reject(placementTimeout())
        : Promise.resolve({ orderId: 7, permId: 8, initialStatus: "Submitted", message: "ok" });
    });
    const { POST } = await import("../app/api/orders/place/route");

    expect((await POST(placeReq(STOCK))).status).toBe(504);
    const different = await POST(placeReq({ ...STOCK, quantity: 200 }));

    expect(different.status).toBe(200);
    expect(placeCallCount()).toBe(2);
  });
});
