import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * POST /api/orders/place auto-enables `outsideRth` when the market is NOT in RTH,
 * so after-hours orders actually work instead of IB holding them to the next open
 * (the 2026-06-22 "can't place after-hours orders" bug). An explicit caller value
 * wins. Asserts the body forwarded to FastAPI /orders/place.
 */
const mockRadonFetch = vi.fn();
const mockReadDataFile = vi.fn();
const mockReadOrdersSnapshotFromDb = vi.fn();
const mockMarketState = vi.fn();

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: class extends Error {
    status = 0;
    detail = "";
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));
vi.mock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));
vi.mock("@tools/schemas/ib-orders", () => ({ OrdersData: {} }));
vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  readOrdersSnapshotFromDb: mockReadOrdersSnapshotFromDb,
}));
vi.mock("@/lib/serviceHealthWindows", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/serviceHealthWindows")>()),
  getMarketStateFromDate: mockMarketState,
}));

async function place(body: Record<string, unknown>) {
  const { POST } = await import("../app/api/orders/place/route");
  return POST(
    new Request("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}
function forwardedOutsideRth(): unknown {
  const body = mockRadonFetch.mock.calls[0]?.[1]?.body as string;
  return JSON.parse(body).outsideRth;
}

// BUY avoids the naked-short guard so the assertion isolates outsideRth.
const ORDER = { type: "stock", symbol: "TSLL", action: "BUY", quantity: 100, limitPrice: 13.4 };

describe("POST /api/orders/place — outsideRth (after-hours)", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset().mockResolvedValue({ status: "ok", permId: 1, orderId: 1 });
    mockReadDataFile.mockReset().mockResolvedValue({ ok: true, data: { positions: [] } });
    mockReadOrdersSnapshotFromDb.mockReset().mockResolvedValue({
      last_sync: "2026-06-22T20:00:00Z",
      open_orders: [],
      executed_orders: [],
      open_count: 0,
      executed_count: 0,
    });
    mockMarketState.mockReset();
  });

  it("auto-enables outsideRth when the market is closed", async () => {
    mockMarketState.mockReturnValue("closed");
    await place(ORDER);
    expect(forwardedOutsideRth()).toBe(true);
  });

  it("auto-enables outsideRth during extended hours", async () => {
    mockMarketState.mockReturnValue("extended");
    await place(ORDER);
    expect(forwardedOutsideRth()).toBe(true);
  });

  it("keeps outsideRth false during RTH (open)", async () => {
    mockMarketState.mockReturnValue("open");
    await place(ORDER);
    expect(forwardedOutsideRth()).toBe(false);
  });

  it("respects an explicit caller value over the auto-default", async () => {
    mockMarketState.mockReturnValue("closed");
    await place({ ...ORDER, outsideRth: false });
    expect(forwardedOutsideRth()).toBe(false);
  });
});
