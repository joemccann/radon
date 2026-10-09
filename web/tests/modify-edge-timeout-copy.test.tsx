/**
 * @vitest-environment jsdom
 *
 * Caddy's empty 504 on POST /api/orders/modify is not a rejected modify.
 * res.json() throws, and the catch used to say "Modify request failed",
 * which sent the operator back to replace an order IB had already accepted.
 */
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { OpenOrder } from "@/lib/types";
import { OrderActionsProvider, useOrderActions } from "@/lib/OrderActionsContext";

const UNKNOWN =
  "Modify status unknown. The change may already be at IB. Check the order before sending it again.";

function stockOrder(): OpenOrder {
  return {
    orderId: 2,
    permId: 1002,
    symbol: "CRWD",
    contract: {
      conId: 20,
      symbol: "CRWD",
      secType: "BAG",
      strike: null,
      right: null,
      expiry: null,
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 100,
    limitPrice: 5.6,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 100,
    avgFillPrice: null,
    tif: "DAY",
  };
}

function edgeTimeout() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("", {
      status: 504,
      headers: { "Content-Type": "text/html", Server: "Caddy" },
    })),
  );
}

function wrapper({ children }: { children: ReactNode }) {
  return <OrderActionsProvider>{children}</OrderActionsProvider>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("requestModify on a Caddy 504", () => {
  it("does not say the modify failed", async () => {
    edgeTimeout();
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestModify(stockOrder(), {
        replaceOrder: {
          type: "combo",
          symbol: "CRWD",
          action: "BUY",
          quantity: 100,
          limitPrice: 5.75,
          legs: [
            { expiry: "20261120", strike: 260, right: "P", action: "BUY", ratio: 1 },
            { expiry: "20261120", strike: 230, right: "P", action: "SELL", ratio: 1 },
          ],
        },
      });
    });
    const notes = result.current.drainNotifications();
    expect(notes).toEqual([{ type: "warning", message: UNKNOWN, duration: 0 }]);
    expect(notes[0].message).not.toMatch(/failed|try again/i);
    expect(result.current.pendingModifies.size).toBe(0);
  });

  it("keeps polling a same-order price change that the edge cut off", async () => {
    edgeTimeout();
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(async () => {
      await result.current.requestModify(stockOrder(), { newPrice: 5.75 });
    });
    expect(result.current.drainNotifications()[0]?.message).toBe(UNKNOWN);
    expect(result.current.pendingModifies.get(1002)?.newPrice).toBe(5.75);
  });
});
