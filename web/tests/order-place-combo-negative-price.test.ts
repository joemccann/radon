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

describe("POST /api/orders/place — signed combo prices", () => {
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

  it("allows a negative combo limit price and forwards the sign unchanged", async () => {
    mockReadDataFile.mockResolvedValueOnce({ ok: false, error: "skip naked-short fixture" });

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
          type: "combo",
          symbol: "IWM",
          action: "SELL",
          quantity: 50,
          limitPrice: -0.4,
          tif: "DAY",
          legs: [
            { expiry: "20260326", strike: 247, right: "C", action: "BUY", ratio: 1 },
            { expiry: "20260326", strike: 243, right: "P", action: "SELL", ratio: 1 },
          ],
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

    const forwarded = JSON.parse(mockRadonFetch.mock.calls[0][1].body as string);
    expect(forwarded.type).toBe("combo");
    expect(forwarded.symbol).toBe("IWM");
    expect(forwarded.limitPrice).toBe(-0.4);
  });
});
