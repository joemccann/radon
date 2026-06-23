import { beforeEach, describe, expect, it, vi } from "vitest";

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
}));

const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({
  readDataFile: mockReadDataFile,
}));

const mockReadOrdersSnapshotFromDb = vi.fn();
vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  readOrdersSnapshotFromDb: mockReadOrdersSnapshotFromDb,
}));

vi.mock("@tools/schemas/ib-orders", () => ({
  OrdersData: {},
}));

describe("POST /api/orders/place — closing a held long option", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockReadDataFile.mockReset();
    mockReadOrdersSnapshotFromDb.mockReset();
    mockReadOrdersSnapshotFromDb.mockResolvedValue({
      last_sync: "2026-06-22T20:00:00Z",
      open_orders: [],
      executed_orders: [],
      open_count: 0,
      executed_count: 0,
    });
  });

  it("allows SELL option payloads that match a held long call contract", async () => {
    mockReadDataFile.mockResolvedValueOnce({
      ok: true,
      data: {
        positions: [
          {
            ticker: "WULF",
            expiry: "2027-01-15",
            structure_type: "Long Call",
            contracts: 77,
            direction: "LONG",
            legs: [
              { direction: "LONG", type: "Call", contracts: 77, strike: 17 },
            ],
          },
        ],
      },
    });

    mockRadonFetch
      .mockResolvedValueOnce({
        status: "ok",
        orderId: 12345,
        permId: 54321,
        initialStatus: "Submitted",
        message: "Order placed successfully",
      })
      .mockResolvedValueOnce({});

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "option",
          symbol: "WULF",
          action: "SELL",
          quantity: 77,
          limitPrice: 4.47,
          expiry: "20270115",
          strike: 17,
          right: "C",
        }),
      }),
    );

    expect(res.status).toBe(200);
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/orders/place",
      expect.objectContaining({
        method: "POST",
      }),
    );

    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.orderId).toBe(12345);
  });
});
