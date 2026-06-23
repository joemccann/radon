/**
 * Bug guard: /api/orders must return Cache-Control: no-store on every
 * response so browsers and intermediaries never serve a stale snapshot.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

const dbOrders = {
  last_sync: "2026-05-08T20:00:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

beforeEach(() => {
  vi.resetModules();
  vi.doMock("@/lib/orders/readOrdersFromDb", () => ({
    readOrdersSnapshotFromDb: vi.fn().mockResolvedValue(dbOrders),
  }));
  vi.doMock("@/lib/radonApi", () => ({
    radonFetch: vi.fn().mockResolvedValue(undefined),
  }));
});

afterEach(() => {
  vi.clearAllMocks();
});

function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

describe("/api/orders — Cache-Control: no-store", () => {
  it("GET sets no-store on the success path", async () => {
    const { GET } = await import("../app/api/orders/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("POST sets no-store on the success path", async () => {
    const { POST } = await import("../app/api/orders/route");
    const res = await POST();
    expectNoStore(res);
  });
});
