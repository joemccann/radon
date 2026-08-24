import { beforeEach, describe, expect, it, vi } from "vitest";

const mockRadonFetch = vi.fn();
const mockReadDataFile = vi.fn();

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  radonErrorDetailText: (detail: unknown) =>
    typeof detail === "string" ? detail : JSON.stringify(detail),
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

vi.mock("@tools/data-reader", () => ({
  readDataFile: mockReadDataFile,
}));

vi.mock("@tools/schemas/ib-orders", () => ({
  OrdersData: {},
}));

describe("POST /api/orders/place upstream error propagation", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockReadDataFile.mockReset();
    mockReadDataFile.mockResolvedValue({ ok: true, data: { positions: [] } });
  });

  it("preserves upstream status and detail instead of wrapping with RadonApiError text", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mockRadonFetch.mockRejectedValueOnce(
      new RadonApiError(
        502,
        "IB error 201: Order rejected - reason:YOUR ORDER IS NOT ACCEPTED.",
      ),
    );

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "stock",
          symbol: "PLTR",
          action: "BUY",
          quantity: 100,
          limitPrice: 150,
        }),
      }),
    );

    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toBe("IB error 201: Order rejected - reason:YOUR ORDER IS NOT ACCEPTED.");
    expect(body.error).not.toContain("Radon API 502:");
  });
});
