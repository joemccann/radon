/**
 * @vitest-environment node
 *
 * Tests for /api/orders after the Turso source-of-truth flip.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const dbOrders = {
  last_sync: "2026-05-07T13:30:00Z",
  open_orders: [
    {
      orderId: 1,
      permId: 1,
      symbol: "TSLA",
      contract: {
        conId: 1,
        symbol: "TSLA",
        secType: "OPT",
        strike: 200,
        right: "C",
        expiry: "20260530",
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 1,
      limitPrice: 5,
      auxPrice: null,
      status: "PreSubmitted",
      filled: 0,
      remaining: 1,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

const emptyOrders = {
  last_sync: "",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const mockReadOrdersSnapshotFromDb = vi.fn();
const mockReadDataFile = vi.fn();
const mockRadonFetch = vi.fn();

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.doMock("@/lib/orders/readOrdersFromDb", () => ({
    readOrdersSnapshotFromDb: mockReadOrdersSnapshotFromDb,
  }));
  vi.doMock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));
  vi.doMock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("/api/orders", () => {
  it("GET returns the Turso-derived orders snapshot", async () => {
    mockReadOrdersSnapshotFromDb.mockResolvedValue(dbOrders);

    const { GET } = await import("../app/api/orders/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.open_count).toBe(1);
    expect(body.open_orders[0].permId).toBe(1);
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("GET returns the empty Turso snapshot when no rows exist", async () => {
    mockReadOrdersSnapshotFromDb.mockResolvedValue(emptyOrders);

    const { GET } = await import("../app/api/orders/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body).toEqual(emptyOrders);
  });

  it("GET returns 500 when the Turso read fails", async () => {
    mockReadOrdersSnapshotFromDb.mockRejectedValue(new Error("database unavailable"));

    const { GET } = await import("../app/api/orders/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(500);
    expect(body.error).toContain("database unavailable");
  });

  it("POST refreshes through FastAPI and returns the Turso snapshot", async () => {
    mockRadonFetch.mockResolvedValue({ ok: true });
    mockReadOrdersSnapshotFromDb.mockResolvedValue(dbOrders);

    const { POST } = await import("../app/api/orders/route");
    const res = await POST();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.last_sync).toBe("2026-05-07T13:30:00Z");
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/orders/refresh",
      expect.objectContaining({ method: "POST" }),
    );
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("POST serves the latest Turso snapshot with a warning when refresh fails", async () => {
    mockRadonFetch.mockRejectedValue(new Error("IB unavailable"));
    mockReadOrdersSnapshotFromDb.mockResolvedValue(dbOrders);

    const { POST } = await import("../app/api/orders/route");
    const res = await POST();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.last_sync).toBe("2026-05-07T13:30:00Z");
    expect(res.headers.get("X-Sync-Warning")).toContain("Turso");
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("POST returns 502 when refresh fails and Turso has no synced snapshot", async () => {
    mockRadonFetch.mockRejectedValue(new Error("IB unavailable"));
    mockReadOrdersSnapshotFromDb.mockResolvedValue(emptyOrders);

    const { POST } = await import("../app/api/orders/route");
    const res = await POST();

    expect(res.status).toBe(502);
  });

  it("POST returns 502 when refresh and Turso read both fail", async () => {
    mockRadonFetch.mockRejectedValue(new Error("IB unavailable"));
    mockReadOrdersSnapshotFromDb.mockRejectedValue(new Error("database unavailable"));

    const { POST } = await import("../app/api/orders/route");
    const res = await POST();
    const body = await res.json();

    expect(res.status).toBe(502);
    expect(body.error).toContain("database unavailable");
  });

  it("exports dynamic = force-dynamic (cache contract)", async () => {
    mockReadOrdersSnapshotFromDb.mockResolvedValue(dbOrders);
    const mod = await import("../app/api/orders/route");
    expect(mod.dynamic).toBe("force-dynamic");
  });
});
