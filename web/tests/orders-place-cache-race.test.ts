/**
 * @vitest-environment node
 *
 * T-169 — the post-mutation orders-cache invalidation, driven behaviourally.
 *
 * `place` / `cancel` / `modify` all do: invalidate → await `/orders/refresh`
 * → invalidate AGAIN in `finally` → read back. Deleting only the second
 * invalidate left every source-text assertion green, because the first call
 * still bridges the regex. The bug it hides is real: a concurrent
 * `GET /api/orders` served DURING the refresh stores the PRE-fill rowset at the
 * current cache generation (`lib/dbCache.ts:108-114`), and the mutating route's
 * own read then hits that entry inside the 2s TTL — so the operator's response
 * omits the change they just made.
 *
 * These tests force the cache on (`RADON_DB_CACHE_FORCE=1`, otherwise
 * `dbCache.ts:88` bypasses it under NODE_ENV=test), hold `/orders/refresh` open
 * on a controlled deferred, race a real GET through it, and assert the mutating
 * route's own response reflects post-refresh Turso state.
 *
 * SAFETY: `radonFetch` and `dbExecute` are mocked. Nothing reaches IB or Turso.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __clearDbCache } from "@/lib/dbCache";

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

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void };

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

/** GTC so `isPriorSessionDayOrder` never filters the row out under any host TZ. */
function openRow(orderId: number, limitPrice: number, quantity = 1) {
  return {
    payload: JSON.stringify({
      orderId,
      permId: orderId * 10,
      symbol: "PLTR",
      contract: { conId: orderId, symbol: "PLTR", secType: "STK" },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: quantity,
      limitPrice,
      auxPrice: null,
      status: "PreSubmitted",
      filled: 0,
      remaining: quantity,
      avgFillPrice: null,
      tif: "GTC",
    }),
    updated_at: new Date().toISOString(),
  };
}

/** Flipped by the test the instant `/orders/refresh` resolves — i.e. the moment
 *  the IB refresh has actually written the new state into Turso. */
let openRows: ReturnType<typeof openRow>[] = [];

function openOrderIds(body: { orders?: { open_orders?: Array<{ orderId: number }> } }): number[] {
  return (body.orders?.open_orders ?? []).map((order) => order.orderId);
}

function refreshCallCount(): number {
  return mockRadonFetch.mock.calls.filter((call) => call[0] === "/orders/refresh").length;
}

beforeEach(() => {
  process.env.RADON_DB_CACHE_FORCE = "1";
  __clearDbCache();
  mockRadonFetch.mockReset();
  mockDbExecute.mockReset();
  mockDbExecute.mockImplementation(({ sql }: { sql: string }) =>
    /FROM\s+open_orders/i.test(sql)
      ? Promise.resolve({ rows: openRows })
      : Promise.resolve({ rows: [] }),
  );
});

afterEach(() => {
  delete process.env.RADON_DB_CACHE_FORCE;
  __clearDbCache();
});

/** Hold `/orders/refresh` open, run a real GET /api/orders through the gap, then
 *  land the refresh with the post-mutation rowset in place. */
async function raceGetThroughRefresh(
  mutation: Promise<Response>,
  postRefreshRows: ReturnType<typeof openRow>[],
  refresh: Deferred<unknown>,
): Promise<{ racedOpenIds: number[] }> {
  await vi.waitFor(() => expect(refreshCallCount()).toBeGreaterThan(0));

  // The concurrent poll: populates the orders cache with PRE-refresh Turso state.
  const { GET } = await import("../app/api/orders/route");
  const raced = await GET();
  const racedBody = await raced.json() as { open_orders: Array<{ orderId: number }> };
  const racedOpenIds = racedBody.open_orders.map((order) => order.orderId);

  // IB's refresh lands: Turso now holds the post-mutation rowset.
  openRows = postRefreshRows;
  refresh.resolve({ status: "ok" });
  await mutation;
  return { racedOpenIds };
}

describe("POST /api/orders/place vs a GET racing the refresh", () => {
  it("returns the just-placed order even though a concurrent GET cached the pre-fill snapshot", async () => {
    openRows = [openRow(1, 5)];
    const refresh = deferred<unknown>();
    mockRadonFetch.mockImplementation((url: string) => {
      if (url === "/orders/place") {
        return Promise.resolve({ orderId: 2, permId: 20, initialStatus: "Submitted", message: "ok" });
      }
      if (url === "/orders/refresh") return refresh.promise;
      return Promise.resolve({});
    });

    const { POST } = await import("../app/api/orders/place/route");
    const posted = POST(new Request("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "stock", symbol: "PLTR", action: "BUY", quantity: 100, limitPrice: 150 }),
    }));

    const { racedOpenIds } = await raceGetThroughRefresh(posted, [openRow(1, 5), openRow(2, 150)], refresh);
    expect(racedOpenIds).toEqual([1]); // the racing poll really did see pre-fill state

    const response = await posted;
    expect(response.status).toBe(200);
    expect(openOrderIds(await response.json())).toContain(2);
  });
});

describe("POST /api/orders/cancel vs a GET racing the refresh", () => {
  it("drops the cancelled order from its own response", async () => {
    openRows = [openRow(1, 5), openRow(2, 150)];
    const refresh = deferred<unknown>();
    mockRadonFetch.mockImplementation((url: string) => {
      if (url === "/orders/cancel") return Promise.resolve({ message: "cancelled" });
      if (url === "/orders/refresh") return refresh.promise;
      return Promise.resolve({});
    });

    const { POST } = await import("../app/api/orders/cancel/route");
    const cancelled = POST(new Request("http://localhost/api/orders/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 2 }),
    }));

    const { racedOpenIds } = await raceGetThroughRefresh(cancelled, [openRow(1, 5)], refresh);
    expect(racedOpenIds).toEqual([1, 2]);

    const response = await cancelled;
    expect(response.status).toBe(200);
    expect(openOrderIds(await response.json())).not.toContain(2);
  });
});

describe("POST /api/orders/modify when the post-modify refresh is unavailable", () => {
  /** T-193. IB has ALREADY ACCEPTED the modify — `/orders/modify` resolved. Only
   *  the follow-up `/orders/refresh` fell over (10s budget, slow Turso). There is
   *  no refreshed book to confirm against, so the route must report "unconfirmed
   *  here, keep polling" (the client's own modify poll is the confirmation
   *  authority) rather than "modify failed". A 502 sends the operator back to
   *  re-modify an order that was already replaced at IB. */
  it("does not report a 502 failure on a modify IB accepted", async () => {
    openRows = [openRow(1, 5)];
    mockRadonFetch.mockImplementation((url: string) => {
      if (url === "/orders/modify") return Promise.resolve({ message: "modified" });
      if (url === "/orders/refresh") return Promise.reject(new Error("timeout after 10000ms"));
      return Promise.resolve({});
    });

    const { POST } = await import("../app/api/orders/modify/route");
    const response = await POST(new Request("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 1, newPrice: 7 }),
    }));
    const body = await response.json() as { status?: string; error?: string; orders: unknown };

    expect(response.status).toBe(200);
    expect(body.status).toBe("ok");
    // Nothing to confirm against: the route must not hand back the pre-modify
    // book, and must not invent one.
    expect(body.orders).toBeNull();
  });

  /** The other half of the distinction: when the refresh DID land and the book
   *  refutes the modify, 502 is still correct. Guards the T-193 fix against
   *  becoming an unconditional 200. */
  it("still returns 502 when the refreshed book refutes the modify", async () => {
    openRows = [openRow(1, 5)];
    mockRadonFetch.mockImplementation((url: string) => {
      if (url === "/orders/modify") return Promise.resolve({ message: "modified" });
      if (url === "/orders/refresh") return Promise.resolve({ status: "ok" });
      return Promise.resolve({});
    });

    const { POST } = await import("../app/api/orders/modify/route");
    const response = await POST(new Request("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 1, newPrice: 7 }),
    }));

    expect(response.status).toBe(502);
    const body = await response.json() as { error: string };
    expect(body.error).toContain("not confirmed");
  });
});

describe("POST /api/orders/modify vs a GET racing the refresh", () => {
  it("confirms the new limit price instead of 502-ing on the stale cached snapshot", async () => {
    openRows = [openRow(1, 5)];
    const refresh = deferred<unknown>();
    mockRadonFetch.mockImplementation((url: string) => {
      if (url === "/orders/modify") return Promise.resolve({ message: "modified" });
      if (url === "/orders/refresh") return refresh.promise;
      return Promise.resolve({});
    });

    const { POST } = await import("../app/api/orders/modify/route");
    const modified = POST(new Request("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 1, newPrice: 7 }),
    }));

    const { racedOpenIds } = await raceGetThroughRefresh(modified, [openRow(1, 7)], refresh);
    expect(racedOpenIds).toEqual([1]);

    const response = await modified;
    const body = await response.json() as { orders?: { open_orders?: Array<{ limitPrice: number }> } };
    expect(response.status).toBe(200);
    expect(body.orders?.open_orders?.[0]?.limitPrice).toBe(7);
  });

  it("returns the combo replacement from the replace path", async () => {
    openRows = [openRow(1, 5)];
    const refresh = deferred<unknown>();
    mockRadonFetch.mockImplementation((url: string) => {
      if (url === "/orders/replace") {
        return Promise.resolve({ message: "replaced", orderId: 3, permId: 30 });
      }
      if (url === "/orders/refresh") return refresh.promise;
      return Promise.resolve({});
    });

    const { POST } = await import("../app/api/orders/modify/route");
    const replaced = POST(new Request("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        orderId: 1,
        replaceOrder: {
          type: "combo",
          symbol: "PLTR",
          action: "BUY",
          quantity: 1,
          limitPrice: 2.5,
          legs: [
            { expiry: "20260918", strike: 150, right: "C", action: "BUY", ratio: 1 },
            { expiry: "20260918", strike: 170, right: "C", action: "SELL", ratio: 1 },
          ],
        },
      }),
    }));

    const { racedOpenIds } = await raceGetThroughRefresh(replaced, [openRow(3, 2.5)], refresh);
    expect(racedOpenIds).toEqual([1]);

    const response = await replaced;
    expect(response.status).toBe(200);
    expect(openOrderIds(await response.json())).toEqual([3]);
  });
});
