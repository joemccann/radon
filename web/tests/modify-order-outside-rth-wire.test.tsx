/**
 * @vitest-environment jsdom
 *
 * requestModify (OrderActionsContext) asserted at the wire.
 *
 * ModifyOrderModal is asserted at `onConfirm`, but the component that OWNS the
 * `/api/orders/modify` fetch was only source-grepped, so the EXT flag could be
 * dropped from the request body (or defaulted to `false` on a price-only
 * change) with every test green. These tests render the REAL provider, record
 * every call, and assert the FULL url, the method, and the exact payload by
 * deep equality.
 */
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { OpenOrder } from "@/lib/types";
import { OrderActionsProvider, useOrderActions } from "@/lib/OrderActionsContext";

type RecordedCall = { url: string; method: string; body: unknown };

function stockOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  return {
    orderId: 2,
    permId: 1002,
    symbol: "TQQQ",
    contract: {
      conId: 20,
      symbol: "TQQQ",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 10,
    limitPrice: 50,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 10,
    avgFillPrice: null,
    tif: "GTC",
    ...overrides,
  };
}

function recordFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      calls.push({ url, method: init?.method ?? "GET", body });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

function wrapper({ children }: { children: ReactNode }) {
  return <OrderActionsProvider>{children}</OrderActionsProvider>;
}

async function modifyOnTheWire(
  order: OpenOrder,
  request: Parameters<ReturnType<typeof useOrderActions>["requestModify"]>[1],
): Promise<RecordedCall> {
  const calls = recordFetch();
  const { result } = renderHook(() => useOrderActions(), { wrapper });
  await act(async () => {
    await result.current.requestModify(order, request);
  });
  expect(calls).toHaveLength(1);
  return calls[0];
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("requestModify puts the EXT flag on the wire", () => {
  it("un-ticking EXT POSTs exactly { orderId, permId, outsideRth: false }", async () => {
    const call = await modifyOnTheWire(stockOrder({ outsideRth: true }), { outsideRth: false });

    expect(call.url).toBe("/api/orders/modify");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ orderId: 2, permId: 1002, outsideRth: false });
  });

  it("ticking EXT on a resting RTH-only order POSTs { orderId, permId, outsideRth: true }", async () => {
    const call = await modifyOnTheWire(stockOrder({ outsideRth: false }), { outsideRth: true });

    expect(call.url).toBe("/api/orders/modify");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ orderId: 2, permId: 1002, outsideRth: true });
  });

  it("a price-only change on an EXT order sends no outsideRth key at all", async () => {
    const call = await modifyOnTheWire(stockOrder({ outsideRth: true }), { newPrice: 51.25 });

    expect(call.url).toBe("/api/orders/modify");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ orderId: 2, permId: 1002, newPrice: 51.25 });
    // `outsideRth ?? false` would silently flip a resting EXT order back to
    // RTH-only on a price change the operator never meant to touch the flag.
    expect("outsideRth" in (call.body as object)).toBe(false);
  });
});
