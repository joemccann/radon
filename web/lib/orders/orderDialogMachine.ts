/**
 * Open-order dialog lifecycle (cancel confirm, modify edit) as one explicit
 * machine. Owners store only order IDENTITY (`OrderRef`) and re-resolve the
 * live `OpenOrder` from the polled book on every render, so guards inside the
 * dialogs (ModifyOrderModal's fill-race reprompt) see live fills instead of a
 * click-time snapshot.
 *
 *   idle ──openCancel──────▶ confirmCancel ──submit──▶ cancelling ──settled──▶ idle
 *   idle ──openCancelCombo─▶ confirmCancelCombo ─submit─▶ cancelling
 *   idle ──openModify──────▶ editing ──submit──▶ modifying ──settled──▶ idle
 *   confirm* / editing ──close | targetGone──▶ idle
 *
 * Opens are accepted only from idle (one dialog at a time). `close` and
 * `targetGone` are ignored while a request is in flight, and `settled` only
 * lands for the request id that started the flight.
 */

import { useCallback, useRef, useState } from "react";
import type { OpenOrder } from "@/lib/types";
import { isTerminal, normalizeIbOrderStatus } from "./ibOrderStatus";

export type OrderRef = { permId: number; orderId: number };

export type ModifySubject =
  | { kind: "single"; ref: OrderRef }
  | { kind: "combo"; refs: OrderRef[] };

export type OrderDialogState =
  | { phase: "idle" }
  | { phase: "confirmCancel"; ref: OrderRef }
  | { phase: "confirmCancelCombo"; refs: OrderRef[] }
  | { phase: "cancelling"; requestId: number; combo: boolean; orders: OpenOrder[] }
  | { phase: "editing"; subject: ModifySubject }
  | { phase: "modifying"; requestId: number; subject: ModifySubject; modalOrder: OpenOrder };

export type OrderDialogEvent =
  | { type: "openCancel"; ref: OrderRef }
  | { type: "openCancelCombo"; refs: OrderRef[] }
  | { type: "openModify"; subject: ModifySubject }
  | { type: "submitCancel"; requestId: number; orders: OpenOrder[] }
  | { type: "submitModify"; requestId: number; modalOrder: OpenOrder }
  | { type: "settled"; requestId: number }
  | { type: "close" }
  | { type: "targetGone" };

export const ORDER_DIALOG_IDLE: OrderDialogState = { phase: "idle" };

/** Pure transition. Returns the SAME state object when the event is rejected. */
export function orderDialogTransition(
  state: OrderDialogState,
  event: OrderDialogEvent,
): OrderDialogState {
  switch (event.type) {
    case "openCancel":
      return state.phase === "idle" ? { phase: "confirmCancel", ref: event.ref } : state;
    case "openCancelCombo":
      return state.phase === "idle" && event.refs.length > 0
        ? { phase: "confirmCancelCombo", refs: event.refs }
        : state;
    case "openModify":
      return state.phase === "idle" ? { phase: "editing", subject: event.subject } : state;
    case "submitCancel":
      if (state.phase !== "confirmCancel" && state.phase !== "confirmCancelCombo") return state;
      if (event.orders.length === 0) return state;
      return {
        phase: "cancelling",
        requestId: event.requestId,
        combo: state.phase === "confirmCancelCombo",
        orders: event.orders,
      };
    case "submitModify":
      if (state.phase !== "editing") return state;
      return {
        phase: "modifying",
        requestId: event.requestId,
        subject: state.subject,
        modalOrder: event.modalOrder,
      };
    case "settled":
      if ((state.phase === "cancelling" || state.phase === "modifying") && state.requestId === event.requestId) {
        return ORDER_DIALOG_IDLE;
      }
      return state;
    case "close":
    case "targetGone":
      if (state.phase === "confirmCancel" || state.phase === "confirmCancelCombo" || state.phase === "editing") {
        return ORDER_DIALOG_IDLE;
      }
      return state;
  }
}

export function isOrderDialogInFlight(state: OrderDialogState): boolean {
  return state.phase === "cancelling" || state.phase === "modifying";
}

/**
 * React binding. `send` applies the transition synchronously against a ref,
 * so two events in the same tick (a double click) see each other, and returns
 * whether the event was accepted.
 */
export function useOrderDialogMachine(): {
  state: OrderDialogState;
  send: (event: OrderDialogEvent) => boolean;
  nextRequestId: () => number;
} {
  const [state, setState] = useState<OrderDialogState>(ORDER_DIALOG_IDLE);
  const stateRef = useRef<OrderDialogState>(ORDER_DIALOG_IDLE);
  const requestSeq = useRef(0);
  const send = useCallback((event: OrderDialogEvent) => {
    const next = orderDialogTransition(stateRef.current, event);
    if (next === stateRef.current) return false;
    stateRef.current = next;
    setState(next);
    return true;
  }, []);
  const nextRequestId = useCallback(() => {
    requestSeq.current += 1;
    return requestSeq.current;
  }, []);
  return { state, send, nextRequestId };
}

export function orderRefOf(order: Pick<OpenOrder, "permId" | "orderId">): OrderRef {
  return { permId: order.permId, orderId: order.orderId };
}

/**
 * Stable identity key. IB assigns `permId` only once the order reaches the
 * broker, so an unacknowledged order (permId 0 / null) keys on `orderId`
 * instead of colliding with every other unacknowledged order.
 */
export function orderIdentityKey(order: { permId?: number | null; orderId?: number | null }): string {
  return order.permId ? `perm:${order.permId}` : `order:${order.orderId ?? ""}`;
}

function matchesRef(ref: OrderRef, order: OpenOrder): boolean {
  return ref.permId ? order.permId === ref.permId : order.orderId === ref.orderId;
}

function stillWorking(order: OpenOrder): boolean {
  return !isTerminal(normalizeIbOrderStatus(order.status));
}

/** The live, still-working order for `ref`, or null when it left the book. */
export function resolveLiveOrder(
  ref: OrderRef,
  openOrders: readonly OpenOrder[],
): OpenOrder | null {
  const live = openOrders.find((order) => matchesRef(ref, order));
  return live && stillWorking(live) ? live : null;
}

/** Live, still-working orders for `refs` (missing ones dropped). */
export function resolveLiveOrders(
  refs: readonly OrderRef[],
  openOrders: readonly OpenOrder[],
): OpenOrder[] {
  const out: OpenOrder[] = [];
  for (const ref of refs) {
    const live = resolveLiveOrder(ref, openOrders);
    if (live) out.push(live);
  }
  return out;
}

/** True when `orders` is exactly the order set named by `refs`. */
export function ordersMatchRefs(orders: readonly OpenOrder[], refs: readonly OrderRef[]): boolean {
  if (orders.length !== refs.length) return false;
  return refs.every((ref) => orders.some((order) => matchesRef(ref, order)));
}

export const ORDER_NO_LONGER_WORKING =
  "Order is no longer working. The dialog was closed.";
