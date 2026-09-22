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

describe("POST /api/orders/place futures multiplier is server-resolved", () => {
  it("forwards the contract-resolved multiplier on the /orders/place wire body", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(futureRequest());
    expect(response.status).toBe(200);

    const chainCall = mockRadonFetch.mock.calls.find(([url]) =>
      typeof url === "string" && url.startsWith("/futures/chain?symbol="),
    );
    expect(chainCall?.[0]).toBe("/futures/chain?symbol=VIX");

    expect(placeCall()).toBeDefined();
    const wireBody = JSON.parse(placeCall()![1].body as string);
    expect(wireBody.type).toBe("future");
    expect(wireBody.conId).toBe(12345);
    expect(wireBody.multiplier).toBe(1000);
  });

  it("resolves the multiplier when the client omits it", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    // quantity varies so the idempotency content hash differs from other tests
    const response = await POST(futureRequest({ multiplier: undefined, quantity: 2 }));
    expect(response.status).toBe(200);
    const wireBody = JSON.parse(placeCall()![1].body as string);
    expect(wireBody.multiplier).toBe(1000);
  });

  it("refuses a client multiplier that mismatches the contract's and fires no order", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(futureRequest({ multiplier: 100 }));
    expect(response.status).toBe(400);
    expect(placeCall()).toBeUndefined();
  });

  it("fails closed when the contract cannot be resolved from the chain", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(futureRequest({ conId: 99999 }));
    expect(response.status).toBe(422);
    expect(placeCall()).toBeUndefined();
  });

  it("fails closed when the chain fetch itself fails", async () => {
    mockRadonFetch.mockImplementation((url: string) => {
      if (url.startsWith("/futures/chain")) return Promise.reject(new Error("down"));
      return Promise.resolve({ status: "ok" });
    });
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(futureRequest());
    expect(response.status).toBe(422);
    expect(placeCall()).toBeUndefined();
  });

  it("resolves by expiry+exchange when conId is absent", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const response = await POST(
      futureRequest({ conId: undefined, expiry: "20261216", multiplier: undefined, quantity: 3 }),
    );
    expect(response.status).toBe(200);
    const wireBody = JSON.parse(placeCall()![1].body as string);
    expect(wireBody.multiplier).toBe(1000);
  });
});
