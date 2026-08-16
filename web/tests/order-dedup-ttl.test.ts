import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * REL-027 / R-051 — the content-hash idempotency TTL is a deliberately SHORT
 * window. At the 300s it was raised to, a genuinely intended identical second
 * clip (scaling into a position; re-placing a stop with the same payload after
 * cancelling it) was suppressed: the route returned the FIRST order's
 * orderId/permId with status "ok", the operator believed two orders were live
 * and held half the intended position.
 *
 * Contract pinned here:
 *  - an identical resubmit at T+10s IS deduped (double-click / transport retry),
 *  - an identical submit past the content-hash TTL PLACES again — 20s later is
 *    a second clip, not a retry,
 *  - an explicit client idempotencyKey states user intent precisely and keeps
 *    its long window.
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
  mockRadonFetch.mockImplementation((url: string) =>
    url === "/orders/place"
      ? Promise.resolve({ orderId: 1, permId: 2, initialStatus: "Submitted", message: "ok" })
      : Promise.resolve({}),
  );
});

describe("R-051 — content-hash TTL is a short window", () => {
  it("dedups an identical resubmit 10s after placement and flags it", async () => {
    vi.useFakeTimers();
    try {
      const { POST } = await import("../app/api/orders/place/route");
      const first = await POST(placeReq(STOCK));
      vi.advanceTimersByTime(10_000);
      const retry = await POST(placeReq(STOCK));

      expect(first.status).toBe(200);
      expect(retry.status).toBe(200);
      expect(placeCallCount()).toBe(1);
      expect((await retry.json()).deduplicated).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("PLACES a genuinely intended identical second clip 20s later", async () => {
    vi.useFakeTimers();
    try {
      const { POST } = await import("../app/api/orders/place/route");
      await POST(placeReq(STOCK));
      vi.advanceTimersByTime(20_000); // scaling in: same payload, real intent
      const secondClip = await POST(placeReq(STOCK));

      expect(secondClip.status).toBe(200);
      expect(placeCallCount()).toBe(2); // NOT suppressed
      expect((await secondClip.json()).deduplicated).toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("places again immediately past the content-hash TTL boundary", async () => {
    vi.useFakeTimers();
    try {
      const { CONTENT_HASH_TTL_MS } = await import("@/lib/orders/orderIdempotency");
      const { POST } = await import("../app/api/orders/place/route");
      await POST(placeReq(STOCK));
      vi.advanceTimersByTime(CONTENT_HASH_TTL_MS + 1_000);
      await POST(placeReq(STOCK));
      expect(placeCallCount()).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the long window for an explicit client idempotencyKey", async () => {
    vi.useFakeTimers();
    try {
      const { POST } = await import("../app/api/orders/place/route");
      await POST(placeReq({ ...STOCK, idempotencyKey: "intent-clip-1" }));
      vi.advanceTimersByTime(20_000);
      const retry = await POST(placeReq({ ...STOCK, idempotencyKey: "intent-clip-1" }));

      expect(placeCallCount()).toBe(1); // same stated intent → still deduped
      expect((await retry.json()).deduplicated).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
