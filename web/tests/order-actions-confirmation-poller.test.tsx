/**
 * @vitest-environment jsdom
 *
 * Audit 2026-11-03 §1 #2 and #3: cancel / modify confirmation in
 * OrderActionsProvider, asserted on the wire.
 *
 *  - absence from `open_orders` is not a cancel: IB drops filled orders too
 *  - a second cancel on a pending permId sends nothing
 *  - one shared poller for every pending permId, GET between producer
 *    refreshes, and a 429 backs the refresh off instead of burning a tick
 *  - cancel and modify on one permId own separate loops; unmount stops both
 *  - a fill during a modify is not a sticky "not confirmed" error
 */
import type { ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExecutedOrder, OpenOrder, OrdersData } from "@/lib/types";
import { OrderActionsProvider, useOrderActions } from "@/lib/OrderActionsContext";

type Call = { url: string; method: string; body: unknown };

function order(overrides: Partial<OpenOrder> = {}): OpenOrder {
  return {
    orderId: 11,
    permId: 1001,
    symbol: "AAPL",
    contract: { conId: 265598, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 100,
    limitPrice: 5,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 100,
    avgFillPrice: null,
    tif: "DAY",
    ...overrides,
  };
}

function execution(permId: number, quantity: number): ExecutedOrder {
  return {
    execId: `exec-${permId}-${quantity}`,
    permId,
    orderId: 11,
    symbol: "AAPL",
    contract: { conId: 265598, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    side: "BOT",
    quantity,
    avgPrice: 5,
    commission: 1,
    realizedPNL: null,
    time: "2026-11-03T15:00:00Z",
    exchange: "SMART",
  };
}

function snapshot(open: OpenOrder[], executed: ExecutedOrder[] = []): OrdersData {
  return {
    last_sync: "2026-11-03T15:00:00Z",
    open_orders: open,
    executed_orders: executed,
    open_count: open.length,
    executed_count: executed.length,
  };
}

let calls: Call[] = [];
let ordersResponder: (method: string) => Response;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(data: OrdersData): void {
  ordersResponder = () => json(data);
}

beforeEach(() => {
  vi.useFakeTimers();
  calls = [];
  serve(snapshot([order()]));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/orders/cancel" || url === "/api/orders/modify") return json({ status: "ok" });
      if (url === "/api/orders") return ordersResponder(method);
      return json({}, 404);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function wrapper({ children }: { children: ReactNode }) {
  return <OrderActionsProvider>{children}</OrderActionsProvider>;
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function pollCalls(): Call[] {
  return calls.filter((c) => c.url === "/api/orders");
}

describe("cancel confirmation reads executions before saying cancelled", () => {
  it("a fill during the cancel window is reported as a fill, with no cancelled row", async () => {
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestCancel(order());
    });
    serve(snapshot([], [execution(1001, 100)]));
    await advance(5_000);

    const notes = result.current.drainNotifications();
    expect(notes.map((n) => n.message)).toEqual(["AAPL order filled before the cancel landed"]);
    expect(notes[0].type).toBe("warning");
    expect(result.current.cancelledOrders).toEqual([]);
    expect(result.current.pendingCancels.size).toBe(0);
  });

  it("gone with no executions is a confirmed cancel", async () => {
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestCancel(order());
    });
    serve(snapshot([]));
    await advance(5_000);

    expect(result.current.drainNotifications()).toEqual([{ type: "success", message: "AAPL order cancelled" }]);
    expect(result.current.cancelledOrders.map((c) => c.permId)).toEqual([1001]);
  });

  it("a combo gone with leg executions is no longer working, never cancelled", async () => {
    const bag = order({ contract: { conId: 28812380, symbol: "AAPL", secType: "BAG", strike: null, right: null, expiry: null } });
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestCancel(bag);
    });
    serve(snapshot([], [execution(1001, 100), execution(1001, 100)]));
    await advance(5_000);

    const [note] = result.current.drainNotifications();
    expect(note.message).toMatch(/no longer working/);
    expect(note.message).not.toMatch(/cancelled/);
    expect(result.current.cancelledOrders).toEqual([]);
  });
});

describe("cancel request gate", () => {
  it("sends the cancel once with the full payload, and a second click on the pending permId sends nothing", async () => {
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestCancel(order());
    });
    expect(calls).toEqual([
      { url: "/api/orders/cancel", method: "POST", body: { orderId: 11, permId: 1001 } },
    ]);

    await act(async () => {
      await result.current.requestCancel(order());
    });
    expect(calls.filter((c) => c.url === "/api/orders/cancel")).toHaveLength(1);
  });
});

describe("one shared poller against the orders-refresh rate limit", () => {
  it("polls once per tick for every pending order, with GET between producer refreshes", async () => {
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    serve(snapshot([order(), order({ permId: 1002, orderId: 12, symbol: "MSFT" })]));
    await act(async () => {
      await result.current.requestCancel(order());
      await result.current.requestCancel(order({ permId: 1002, orderId: 12, symbol: "MSFT" }));
    });

    await advance(5_000);
    expect(pollCalls()).toEqual([{ url: "/api/orders", method: "POST", body: undefined }]);

    await advance(5_000);
    expect(pollCalls().map((c) => c.method)).toEqual(["POST", "GET"]);

    // Within any 60s window the producer refresh stays under 4 POSTs.
    await advance(55_000);
    const posts = pollCalls().filter((c) => c.method === "POST");
    expect(posts.length).toBeLessThanOrEqual(4);
    expect(pollCalls().length).toBe(13);
  });

  it("a 429 backs the refresh off and does not burn the confirmation budget", async () => {
    ordersResponder = (method) =>
      method === "POST"
        ? json({ error: "Too Many Requests" }, 429)
        : json(snapshot([order()]));
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestCancel(order());
    });

    await advance(5_000);
    expect(pollCalls().map((c) => c.method)).toEqual(["POST"]);
    // Next refresh no sooner than the 30s backoff; reads fill the gap.
    await advance(25_000);
    expect(pollCalls().map((c) => c.method)).toEqual(["POST", "GET", "GET", "GET", "GET", "GET"]);
    expect(result.current.pendingCancels.has(1001)).toBe(true);
  });
});

describe("cancel and modify on one permId", () => {
  it("own separate loops, and unmount stops every poll", async () => {
    const { result, unmount } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestModify(order(), { newPrice: 5.25 });
      await result.current.requestCancel(order());
    });
    await advance(5_000);
    expect(pollCalls()).toHaveLength(1);

    unmount();
    const before = calls.length;
    await advance(120_000);
    expect(calls.length).toBe(before);
  });
});

describe("modify confirmation", () => {
  it("a fill during the modify is a warning, not a sticky error", async () => {
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestModify(order(), { newPrice: 5.25 });
    });
    result.current.drainNotifications();
    serve(snapshot([], [execution(1001, 100)]));
    await advance(5_000);

    const notes = result.current.drainNotifications();
    expect(notes).toEqual([{ type: "warning", message: "AAPL order filled before the modify confirmed" }]);
    expect(result.current.pendingModifies.size).toBe(0);
  });

  it("confirms a modify IB applied", async () => {
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestModify(order(), { newPrice: 5.25 });
    });
    serve(snapshot([order({ limitPrice: 5.25 })]));
    await advance(5_000);

    expect(result.current.drainNotifications()).toEqual([
      { type: "success", message: "AAPL order confirmed (price $5.25)" },
    ]);
    expect(result.current.pendingModifies.size).toBe(0);
  });
});
