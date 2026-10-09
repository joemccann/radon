/**
 * @vitest-environment node
 *
 * RC-D1 — the place route must forward the futures contract multiplier to
 * FastAPI, where order_limits refuses any future order without one (the
 * option's 100 under-counted a 1000-multiplier contract's notional 10x).
 *
 * SAFETY: `radonFetch` and `dbExecute` are mocked. Nothing reaches IB or Turso.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockRadonFetch = vi.fn();
const mockDbExecute = vi.fn();

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  radonFetchText: vi.fn(),
  radonErrorDetailText: (detail: unknown) =>
    (typeof detail === "string" ? detail : JSON.stringify(detail)),
  coerceRadonErrorDetail: (detail: unknown) => detail,
  RadonApiError: class RadonApiError extends Error {
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

vi.mock("@/lib/dbExecute", () => ({
  dbExecute: mockDbExecute,
  DEFAULT_DB_READ_TIMEOUT_MS: 3_000,
  describeDbError: (err: unknown) => String(err),
}));

vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute: mockDbExecute }),
  syncDb: vi.fn().mockResolvedValue(undefined),
  resetDb: vi.fn(),
  getPoolStats: () => ({}),
}));

vi.mock("@tools/data-reader", () => ({
  readDataFile: vi.fn().mockResolvedValue({ ok: true, data: { positions: [] } }),
}));
vi.mock("@tools/schemas/ib-orders", () => ({ OrdersData: {} }));

beforeEach(() => {
  mockRadonFetch.mockReset();
  mockDbExecute.mockReset();
  mockDbExecute.mockResolvedValue({ rows: [] });
  mockRadonFetch.mockImplementation((url: string) => {
    if (url === "/orders/place") {
      return Promise.resolve({
        orderId: 7, permId: 70, initialStatus: "Submitted", message: "ok",
      });
    }
    if (url.startsWith("/futures/chain?symbol=")) {
      return Promise.resolve({
        symbol: "VIX",
        exchange: "CFE",
        contracts: [
          { conId: 12345, expiry: "20261118", exchange: "CFE", multiplier: "1000" },
          { conId: 67890, expiry: "20261216", exchange: "CFE", multiplier: "1000" },
        ],
      });
    }
    return Promise.resolve({ status: "ok" });
  });
});

function futureRequest(overrides: Record<string, unknown> = {}) {
  return new Request("http://localhost/api/orders/place", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "future",
      symbol: "VIX",
      action: "BUY",
      quantity: 1,
      limitPrice: 20,
      conId: 12345,
      exchange: "CFE",
      multiplier: 1000,
      ...overrides,
    }),
  });
}

function placeCall() {
  return mockRadonFetch.mock.calls.find(([url]) => url === "/orders/place");
}

function chainCalls() {
  return mockRadonFetch.mock.calls.filter(([url]) =>
    typeof url === "string" && url.startsWith("/futures/chain?symbol="),
  );
}

describe("POST /api/orders/place futures multiplier is server-resolved", () => {
  it("uses the FUTURES_ROOTS multiplier for VIX and does not call the chain", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(futureRequest());
    expect(response.status).toBe(200);
    expect(chainCalls()).toHaveLength(0);
    const wireBody = JSON.parse(placeCall()![1].body as string);
    expect(wireBody.type).toBe("future");
    expect(wireBody.conId).toBe(12345);
    expect(wireBody.multiplier).toBe(1000);
    const refresh = mockRadonFetch.mock.calls.find(([url]) => url === "/orders/refresh");
    expect(refresh?.[1]?.headers?.["X-Radon-Orders-Fresh"]).toBe("1");
  });

  it("resolves VIX, SPX, NDX, and RUT from the root table when the client omits multiplier", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const expected: Record<string, number> = { VIX: 1000, SPX: 50, NDX: 20, RUT: 50 };
    let quantity = 2;
    for (const [symbol, multiplier] of Object.entries(expected)) {
      mockRadonFetch.mockClear();
      const response = await POST(futureRequest({
        symbol, multiplier: undefined, quantity, conId: 12345,
      }));
      expect(response.status).toBe(200);
      expect(chainCalls()).toHaveLength(0);
      const wireBody = JSON.parse(placeCall()![1].body as string);
      expect(wireBody.multiplier).toBe(multiplier);
      quantity += 1;
    }
  });

  it("refuses a client multiplier that mismatches the root table and fires no order", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(futureRequest({ multiplier: 100 }));
    expect(response.status).toBe(400);
    expect(placeCall()).toBeUndefined();
    expect(chainCalls()).toHaveLength(0);
  });

  it("fails closed when an unknown root's contract is not on the chain", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(futureRequest({ symbol: "CL", conId: 99999, multiplier: undefined }));
    expect(response.status).toBe(422);
    expect(placeCall()).toBeUndefined();
    expect(chainCalls()[0]?.[0]).toBe("/futures/chain?symbol=CL");
  });

  it("fails closed when the chain fetch itself fails for an unknown root", async () => {
    mockRadonFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/futures/chain")) {
        return Promise.reject(new Error("down"));
      }
      return Promise.resolve({ status: "ok" });
    });
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(futureRequest({ symbol: "ES", multiplier: undefined, quantity: 8 }));
    expect(response.status).toBe(422);
    expect(placeCall()).toBeUndefined();
    expect(chainCalls()[0]?.[0]).toBe("/futures/chain?symbol=ES");
  });

  it("resolves an unknown root by expiry+exchange when conId is absent", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(
      futureRequest({
        symbol: "CL",
        conId: undefined,
        expiry: "20261216",
        multiplier: undefined,
        quantity: 3,
      }),
    );
    expect(response.status).toBe(200);
    expect(chainCalls()[0]?.[0]).toBe("/futures/chain?symbol=CL");
    const wireBody = JSON.parse(placeCall()![1].body as string);
    expect(wireBody.multiplier).toBe(1000);
  });
});
