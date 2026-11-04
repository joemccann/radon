/**
 * Pure transitions of the cancel / modify confirmation machine
 * (audit 2026-11-03 §1 #2, #3). Wire behaviour lives in
 * order-actions-confirmation-poller.test.tsx.
 */
import { describe, expect, it } from "vitest";
import type { ExecutedOrder, OpenOrder, OrdersData } from "@/lib/types";
import {
  INITIAL_ORDER_ACTION_STATE,
  POLL_MAX_TICKS,
  REFRESH_MIN_INTERVAL_MS,
  nextRequest,
  resolutionNotice,
  transition,
  type OrderActionEvent,
  type OrderActionState,
  type Resolution,
} from "@/lib/orders/orderActionMachine";

function order(overrides: Partial<OpenOrder> = {}): OpenOrder {
  return {
    orderId: 11,
    permId: 1001,
    symbol: "AAPL",
    contract: { conId: 1, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    action: "SELL",
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

function exec(quantity: number, permId = 1001): ExecutedOrder {
  return {
    execId: `e-${quantity}`,
    permId,
    symbol: "AAPL",
    contract: { conId: 1, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    side: "SLD",
    quantity,
    avgPrice: 5,
    commission: null,
    realizedPNL: null,
    time: "",
    exchange: "",
  };
}

function snap(open: OpenOrder[], executed: ExecutedOrder[] = []): OrdersData {
  return { last_sync: "", open_orders: open, executed_orders: executed, open_count: open.length, executed_count: executed.length };
}

function run(state: OrderActionState, events: OrderActionEvent[]): { state: OrderActionState; resolutions: Resolution[] } {
  const resolutions: Resolution[] = [];
  for (const event of events) {
    const step = transition(state, event);
    state = step.state;
    resolutions.push(...step.resolutions);
  }
  return { state, resolutions };
}

const cancelling = transition(INITIAL_ORDER_ACTION_STATE, { type: "cancelRequested", order: order() }).state;

describe("orderActionMachine", () => {
  it("a second cancel on a pending permId leaves the state untouched", () => {
    const again = transition(cancelling, { type: "cancelRequested", order: order() });
    expect(again.state).toBe(cancelling);
  });

  it("rate-limited ticks never count toward the cap", () => {
    const events = Array.from({ length: POLL_MAX_TICKS * 3 }, (_, i) => ({ type: "rateLimited" as const, now: i }));
    const { state, resolutions } = run(cancelling, events);
    expect(resolutions).toEqual([]);
    expect(state.cancels.get("perm:1001")?.ticks).toBe(0);
  });

  it("backs the refresh off on 429 and reads in between", () => {
    const limited = transition(cancelling, { type: "rateLimited", now: 1_000 }).state;
    expect(nextRequest(limited, 1_000 + 29_999)).toBe("read");
    expect(nextRequest(limited, 1_000 + 30_000)).toBe("refresh");
    const twice = transition(limited, { type: "rateLimited", now: 31_000 }).state;
    expect(twice.refresh.nextAt).toBe(31_000 + 60_000);
  });

  it("schedules the next refresh after a refreshed snapshot only", () => {
    const refreshed = transition(cancelling, { type: "snapshot", data: snap([order()]), now: 5_000, refreshed: true }).state;
    expect(refreshed.refresh.nextAt).toBe(5_000 + REFRESH_MIN_INTERVAL_MS);
    const read = transition(refreshed, { type: "snapshot", data: snap([order()]), now: 10_000, refreshed: false }).state;
    expect(read.refresh).toEqual(refreshed.refresh);
  });

  it("names a partial fill then cancel from the executions", () => {
    const { resolutions } = run(cancelling, [{ type: "snapshot", data: snap([], [exec(40)]), now: 0, refreshed: true }]);
    expect(resolutions).toMatchObject([{ kind: "cancel", outcome: "partiallyFilledThenCancelled", filledQuantity: 40 }]);
    expect(resolutionNotice(resolutions[0])?.message).toBe("AAPL order partially filled (40 of 100), rest cancelled");
  });

  it("a listed order whose status is Filled is a fill, not working", () => {
    const { resolutions } = run(cancelling, [
      { type: "snapshot", data: snap([order({ status: "Filled" })]), now: 0, refreshed: true },
    ]);
    expect(resolutions).toMatchObject([{ outcome: "filledDuringCancel" }]);
  });

  it("a listed order still PendingCancel keeps waiting", () => {
    const { state, resolutions } = run(cancelling, [
      { type: "snapshot", data: snap([order({ status: "PendingCancel" })]), now: 0, refreshed: true },
    ]);
    expect(resolutions).toEqual([]);
    expect(state.cancels.get("perm:1001")?.ticks).toBe(1);
  });

  it("failed polls time out as unconfirmed, never as 'still open'", () => {
    const events = Array.from({ length: POLL_MAX_TICKS }, () => ({ type: "pollFailed" as const }));
    const { state, resolutions } = run(cancelling, events);
    expect(state.cancels.size).toBe(0);
    expect(resolutionNotice(resolutions[0])?.message).toMatch(/not confirmed/);
  });

  it("cancel and modify on one permId keep separate counters", () => {
    const both = transition(cancelling, { type: "modifyRequested", order: order(), newPrice: 6 }).state;
    const { state } = run(both, [
      { type: "pollFailed" },
      { type: "modifyRequested", order: order(), newPrice: 6.5 },
    ]);
    expect(state.cancels.get("perm:1001")?.ticks).toBe(1);
    expect(state.modifies.get("perm:1001")?.ticks).toBe(0);
  });

  it("a modify on an order its own cancel removed is reported once, by the cancel", () => {
    const both = transition(cancelling, { type: "modifyRequested", order: order(), newPrice: 6 }).state;
    const { state, resolutions } = run(both, [{ type: "snapshot", data: snap([]), now: 0, refreshed: true }]);
    expect(state.modifies.size).toBe(0);
    const notices = resolutions.map(resolutionNotice).filter(Boolean);
    expect(notices).toEqual([{ type: "success", message: "AAPL order cancelled" }]);
  });

  it("a modify on an order gone without fills is no longer working, not a sticky error", () => {
    const modifying = transition(INITIAL_ORDER_ACTION_STATE, { type: "modifyRequested", order: order(), newPrice: 6 }).state;
    const { resolutions } = run(modifying, [{ type: "snapshot", data: snap([]), now: 0, refreshed: true }]);
    const notice = resolutionNotice(resolutions[0]);
    expect(notice?.type).toBe("warning");
    expect(notice?.duration).toBeUndefined();
  });
});
