import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Audit 2026-11-03 §1 #4: `initialStatus: "Unknown"` means IB never
 * acknowledged the order, not that IB rejected it. The route used to treat it
 * as a deterministic rejection: 502 plus a cleared idempotency key, so the
 * operator's retry could place a second live order on top of the first.
 */

const mockRadonFetch = vi.fn();

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  radonErrorDetailText: (detail: unknown) => typeof detail === "string" ? detail : JSON.stringify(detail),
  RadonApiError: class extends Error {
    status: number;
    detail: string | Record<string, unknown>;
    constructor(status: number, detail: string | Record<string, unknown>) {
      super(`Radon API ${status}`);
      this.name = "RadonApiError";
      this.status = status;
      this.detail = detail;
    }
  },
}));
vi.mock("@tools/data-reader", () => ({ readDataFile: vi.fn(async () => ({ ok: true, data: { positions: [] } })) }));
vi.mock("@tools/schemas/ib-orders", () => ({ OrdersData: {} }));

const STOCK = { type: "stock", symbol: "PLTR", action: "BUY", quantity: 100, limitPrice: 150 };

function placeReq(body: Record<string, unknown>): Request {
  return new Request("http://localhost/api/orders/place", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function placeCallCount(): number {
  return mockRadonFetch.mock.calls.filter((c) => c[0] === "/orders/place").length;
}

function placeReturns(result: Record<string, unknown>): void {
  mockRadonFetch.mockImplementation((url: string) =>
    url === "/orders/place" ? Promise.resolve(result) : Promise.resolve({}),
  );
}

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
});

describe("POST /api/orders/place: Unknown is indeterminate, never a rejection", () => {
  it.each(["Unknown", "PendingSubmit", "ApiPending"])(
    "answers %s with no permId as 504 indeterminate and holds the key",
    async (initialStatus) => {
      placeReturns({ status: "ok", orderId: 41, permId: 0, initialStatus });
      const { POST } = await import("../app/api/orders/place/route");

      const first = await POST(placeReq(STOCK));
      const retry = await POST(placeReq(STOCK));

      expect(first.status).toBe(504);
      const body = await first.json();
      expect(body.code).toBe("UPSTREAM_TIMEOUT_ORDER_INDETERMINATE");
      expect(body.error).not.toMatch(/rejected/i);
      expect(retry.status).toBe(504);
      expect(placeCallCount()).toBe(1);
    },
  );

  it("an acknowledged order (nonzero permId) is a success even with status Unknown", async () => {
    placeReturns({ status: "ok", orderId: 41, permId: 9001, initialStatus: "Unknown", message: "ok" });
    const { POST } = await import("../app/api/orders/place/route");

    const res = await POST(placeReq(STOCK));

    expect(res.status).toBe(200);
    expect((await res.json()).permId).toBe(9001);
  });

  it("an explicit Rejected status is still a deterministic, retryable rejection", async () => {
    placeReturns({ status: "ok", orderId: 41, permId: 9001, initialStatus: "Rejected" });
    const { POST } = await import("../app/api/orders/place/route");

    const first = await POST(placeReq(STOCK));
    const retry = await POST(placeReq(STOCK));

    expect(first.status).toBe(502);
    expect((await first.json()).error).toMatch(/Rejected/);
    expect(retry.status).toBe(502);
    expect(placeCallCount()).toBe(2);
  });
});
