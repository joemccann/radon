import { describe, expect, it } from "vitest";
import type { OpenOrder } from "@/lib/types";
import {
  ORDER_DIALOG_IDLE,
  orderDialogTransition,
  orderIdentityKey,
  resolveLiveOrder,
  resolveLiveOrders,
  type OrderDialogState,
} from "@/lib/orders/orderDialogMachine";

function order(over: Partial<OpenOrder> = {}): OpenOrder {
  return {
    orderId: 1,
    permId: 101,
    symbol: "AAPL",
    contract: { conId: 1, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 10,
    limitPrice: 100,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 10,
    avgFillPrice: null,
    tif: "DAY",
    ...over,
  };
}

const A = { permId: 101, orderId: 1 };
const B = { permId: 202, orderId: 2 };

describe("orderDialogTransition", () => {
  it("opens only from idle: cancel and modify can never be open at once", () => {
    const cancel = orderDialogTransition(ORDER_DIALOG_IDLE, { type: "openCancel", ref: A });
    expect(cancel.phase).toBe("confirmCancel");
    const both = orderDialogTransition(cancel, { type: "openModify", subject: { kind: "single", ref: B } });
    expect(both).toBe(cancel);
    const edit = orderDialogTransition(ORDER_DIALOG_IDLE, { type: "openModify", subject: { kind: "single", ref: A } });
    expect(orderDialogTransition(edit, { type: "openCancel", ref: B })).toBe(edit);
    expect(orderDialogTransition(edit, { type: "openCancelCombo", refs: [B] })).toBe(edit);
  });

  it("close and targetGone are ignored while a request is in flight", () => {
    const confirm: OrderDialogState = { phase: "confirmCancel", ref: A };
    const flying = orderDialogTransition(confirm, { type: "submitCancel", requestId: 1, orders: [order()] });
    expect(flying.phase).toBe("cancelling");
    expect(orderDialogTransition(flying, { type: "close" })).toBe(flying);
    expect(orderDialogTransition(flying, { type: "targetGone" })).toBe(flying);

    const edit: OrderDialogState = { phase: "editing", subject: { kind: "single", ref: A } };
    const modifying = orderDialogTransition(edit, { type: "submitModify", requestId: 2, modalOrder: order() });
    expect(modifying.phase).toBe("modifying");
    expect(orderDialogTransition(modifying, { type: "close" })).toBe(modifying);
  });

  it("a stale settle never closes a newer flight", () => {
    const flying: OrderDialogState = { phase: "cancelling", requestId: 2, combo: false, orders: [order()] };
    expect(orderDialogTransition(flying, { type: "settled", requestId: 1 })).toBe(flying);
    expect(orderDialogTransition(flying, { type: "settled", requestId: 2 })).toEqual(ORDER_DIALOG_IDLE);
    // A settle arriving after the operator moved on to a fresh dialog is inert.
    const fresh: OrderDialogState = { phase: "confirmCancel", ref: B };
    expect(orderDialogTransition(fresh, { type: "settled", requestId: 2 })).toBe(fresh);
  });

  it("submit is accepted once: a second submit while in flight is rejected", () => {
    const confirm: OrderDialogState = { phase: "confirmCancelCombo", refs: [A, B] };
    const flying = orderDialogTransition(confirm, { type: "submitCancel", requestId: 1, orders: [order()] });
    expect(flying).toMatchObject({ phase: "cancelling", combo: true });
    expect(orderDialogTransition(flying, { type: "submitCancel", requestId: 2, orders: [order()] })).toBe(flying);
  });

  it("closing confirm and editing states returns to idle", () => {
    expect(orderDialogTransition({ phase: "confirmCancel", ref: A }, { type: "close" })).toEqual(ORDER_DIALOG_IDLE);
    expect(
      orderDialogTransition({ phase: "editing", subject: { kind: "single", ref: A } }, { type: "targetGone" }),
    ).toEqual(ORDER_DIALOG_IDLE);
  });
});

describe("order identity", () => {
  it("keys on permId, falling back to orderId when IB has not assigned one", () => {
    expect(orderIdentityKey({ permId: 101, orderId: 1 })).toBe("perm:101");
    expect(orderIdentityKey({ permId: 0, orderId: 7 })).not.toBe(orderIdentityKey({ permId: 0, orderId: 8 }));
    expect(orderIdentityKey({ permId: null, orderId: 7 })).toBe("order:7");
  });

  it("resolves the live working order and drops terminal or missing ones", () => {
    const live = order({ filled: 4, remaining: 6 });
    expect(resolveLiveOrder(A, [live])).toBe(live);
    expect(resolveLiveOrder(A, [order({ status: "Filled" })])).toBeNull();
    expect(resolveLiveOrder(A, [order({ status: "cancelled" })])).toBeNull();
    expect(resolveLiveOrder(A, [])).toBeNull();
    // permId 0: match by orderId instead.
    const unacked = order({ permId: 0, orderId: 9 });
    expect(resolveLiveOrder({ permId: 0, orderId: 9 }, [order(), unacked])).toBe(unacked);
    expect(resolveLiveOrders([A, B], [live])).toEqual([live]);
  });
});
