/**
 * Cancel / modify confirmation machine (audit 2026-11-03 §1 #2, #3).
 *
 * One entry per order identity (`orderIdentityKey`: permId, else orderId for
 * an order IB has not acknowledged) per action kind, so a cancel and a modify
 * on the same order never share a counter. Every pending entry is confirmed by ONE shared
 * poller: `nextRequest` picks a producer refresh (POST /api/orders, rate
 * limited to 4/min per user) at most every REFRESH_MIN_INTERVAL_MS and a
 * cached read (GET) otherwise. A 429 backs the refresh off and is not an
 * observation, so it never counts toward the cap.
 *
 * An order missing from `open_orders` is NOT proof of a cancel: IB drops a
 * filled order from that list too. The same snapshot's `executed_orders`
 * names the outcome; when the fills cannot be sized (BAG legs) the order is
 * reported as no longer working, never as cancelled.
 *
 * Pure: no timers, no fetch, no React. The provider owns those.
 */

import type { OpenOrder, OrdersData } from "@/lib/types";
import { FILLED, TERMINAL_FAILED, normalizeIbOrderStatus } from "./ibOrderStatus";
import { orderIdentityKey } from "./orderDialogMachine";

export const POLL_INTERVAL_MS = 5_000;
/** ~2 min of 5s ticks. */
export const POLL_MAX_TICKS = 24;
/** At most 3 producer refreshes a minute, under the 4/min orders-refresh limit. */
export const REFRESH_MIN_INTERVAL_MS = 20_000;
export const REFRESH_BACKOFF_MIN_MS = 30_000;
export const REFRESH_BACKOFF_MAX_MS = 120_000;

export type CancelPending = {
  kind: "cancel";
  order: OpenOrder;
  ticks: number;
  observedWorking: boolean;
};

export type ModifyPending = {
  kind: "modify";
  order: OpenOrder;
  newPrice?: number;
  newQuantity?: number;
  ticks: number;
  observedWorking: boolean;
};

export type OrderActionState = {
  /** Keyed by `orderIdentityKey`. */
  cancels: ReadonlyMap<string, CancelPending>;
  modifies: ReadonlyMap<string, ModifyPending>;
  refresh: { nextAt: number; backoffMs: number };
};

export type CancelOutcome =
  | { outcome: "confirmedCancelled" }
  | { outcome: "partiallyFilledThenCancelled"; filledQuantity: number }
  | { outcome: "filledDuringCancel" }
  | { outcome: "noLongerWorking" }
  | { outcome: "timedOut"; observedWorking: boolean };

export type ModifyOutcome =
  | { outcome: "confirmedModified" }
  | { outcome: "filledDuringModify" }
  | { outcome: "noLongerWorking" }
  /** The order is gone and a cancel on it is pending: the cancel reports it. */
  | { outcome: "supersededByCancel" }
  | { outcome: "timedOut"; observedWorking: boolean };

export type Resolution =
  | ({ kind: "cancel"; pending: CancelPending } & CancelOutcome)
  | ({ kind: "modify"; pending: ModifyPending } & ModifyOutcome);

export type OrderActionEvent =
  | { type: "cancelRequested"; order: OpenOrder }
  | { type: "modifyRequested"; order: OpenOrder; newPrice?: number; newQuantity?: number }
  /** A usable orders payload. `refreshed`: it came from a producer refresh. */
  | { type: "snapshot"; data: OrdersData; now: number; refreshed: boolean }
  /** The refresh POST answered 429. Not an observation. */
  | { type: "rateLimited"; now: number; retryAfterMs?: number }
  /** A poll that produced no payload (network error, 5xx). Counts as a tick. */
  | { type: "pollFailed" };

export type Transition = { state: OrderActionState; resolutions: Resolution[] };

export const INITIAL_ORDER_ACTION_STATE: OrderActionState = {
  cancels: new Map(),
  modifies: new Map(),
  refresh: { nextAt: 0, backoffMs: 0 },
};

export function hasPending(state: OrderActionState): boolean {
  return state.cancels.size > 0 || state.modifies.size > 0;
}

export function isCancelPending(state: OrderActionState, order: Pick<OpenOrder, "permId" | "orderId">): boolean {
  return state.cancels.has(orderIdentityKey(order));
}

/** Which request the shared poller sends next, or null when nothing is pending. */
export function nextRequest(state: OrderActionState, now: number): "refresh" | "read" | null {
  if (!hasPending(state)) return null;
  return now >= state.refresh.nextAt ? "refresh" : "read";
}

/* ── Observation ─────────────────────────────────────────────────────── */

type Observation =
  | { kind: "working"; live: OpenOrder }
  | { kind: "gone"; fills: "none" | "full" | "unsized" }
  | { kind: "gone"; fills: "partial"; filledQuantity: number };

function sameOrder(order: OpenOrder, candidate: { permId?: number | null; orderId?: number | null }): boolean {
  if (order.permId !== 0 && candidate.permId === order.permId) return true;
  return order.orderId !== 0 && candidate.orderId === order.orderId;
}

function observe(order: OpenOrder, data: OrdersData): Observation {
  const live = data.open_orders.find((o) => sameOrder(order, o));
  const liveStatus = live ? normalizeIbOrderStatus(live.status) : null;
  // A listed order is working unless its status says otherwise; an
  // unrecognised status is never read as gone.
  if (live && !(liveStatus && (FILLED.has(liveStatus) || TERMINAL_FAILED.has(liveStatus)))) {
    return { kind: "working", live };
  }
  if (liveStatus && FILLED.has(liveStatus)) return { kind: "gone", fills: "full" };

  const executions = (data.executed_orders ?? []).filter((e) =>
    order.permId !== 0 ? e.permId === order.permId : sameOrder(order, e),
  );
  if (executions.length === 0) {
    // Fills from an earlier session (GTC) are not in today's executions.
    return order.filled > 0
      ? { kind: "gone", fills: "partial", filledQuantity: order.filled }
      : { kind: "gone", fills: "none" };
  }
  // BAG executions are per leg: their quantities do not size the combo.
  if (order.contract?.secType === "BAG") return { kind: "gone", fills: "unsized" };
  const filledQuantity = executions.reduce((sum, e) => sum + Math.abs(Number(e.quantity) || 0), 0);
  if (filledQuantity >= order.totalQuantity) return { kind: "gone", fills: "full" };
  return { kind: "gone", fills: "partial", filledQuantity };
}

function modifyApplied(pending: ModifyPending, live: OpenOrder): boolean {
  const priceConfirmed = pending.newPrice == null
    || (live.limitPrice != null && Math.abs(live.limitPrice - pending.newPrice) < 0.001);
  const quantityConfirmed = pending.newQuantity == null || live.totalQuantity === pending.newQuantity;
  return priceConfirmed && quantityConfirmed;
}

function cancelStep(pending: CancelPending, obs: Observation): CancelOutcome | CancelPending {
  if (obs.kind === "gone") {
    switch (obs.fills) {
      case "none": return { outcome: "confirmedCancelled" };
      case "partial": return { outcome: "partiallyFilledThenCancelled", filledQuantity: obs.filledQuantity };
      case "full": return { outcome: "filledDuringCancel" };
      case "unsized": return { outcome: "noLongerWorking" };
    }
  }
  const next = { ...pending, ticks: pending.ticks + 1, observedWorking: true };
  return next.ticks >= POLL_MAX_TICKS ? { outcome: "timedOut", observedWorking: true } : next;
}

function modifyStep(
  pending: ModifyPending,
  obs: Observation,
  cancelPending: boolean,
): ModifyOutcome | ModifyPending {
  if (obs.kind === "gone") {
    if (cancelPending) return { outcome: "supersededByCancel" };
    return obs.fills === "full" ? { outcome: "filledDuringModify" } : { outcome: "noLongerWorking" };
  }
  if (modifyApplied(pending, obs.live)) return { outcome: "confirmedModified" };
  const next = { ...pending, ticks: pending.ticks + 1, observedWorking: true };
  return next.ticks >= POLL_MAX_TICKS ? { outcome: "timedOut", observedWorking: true } : next;
}

function isPending<T extends { kind: string }>(value: T | { outcome: string }): value is T {
  return !("outcome" in value);
}

/* ── Transition ──────────────────────────────────────────────────────── */

export function transition(state: OrderActionState, event: OrderActionEvent): Transition {
  switch (event.type) {
    case "cancelRequested": {
      // A second cancel on a pending order is a no-op: one confirmation loop.
      const key = orderIdentityKey(event.order);
      if (state.cancels.has(key)) return { state, resolutions: [] };
      const cancels = new Map(state.cancels);
      cancels.set(key, { kind: "cancel", order: event.order, ticks: 0, observedWorking: false });
      return { state: { ...state, cancels }, resolutions: [] };
    }
    case "modifyRequested": {
      // A newer modify on the same order supersedes the older target.
      const modifies = new Map(state.modifies);
      modifies.set(orderIdentityKey(event.order), {
        kind: "modify",
        order: event.order,
        newPrice: event.newPrice,
        newQuantity: event.newQuantity,
        ticks: 0,
        observedWorking: false,
      });
      return { state: { ...state, modifies }, resolutions: [] };
    }
    case "rateLimited": {
      const backoffMs = Math.min(
        REFRESH_BACKOFF_MAX_MS,
        Math.max(REFRESH_BACKOFF_MIN_MS, state.refresh.backoffMs * 2, event.retryAfterMs ?? 0),
      );
      return { state: { ...state, refresh: { nextAt: event.now + backoffMs, backoffMs } }, resolutions: [] };
    }
    case "pollFailed": {
      const resolutions: Resolution[] = [];
      const cancels = new Map<string, CancelPending>();
      for (const [key, pending] of state.cancels) {
        const next = { ...pending, ticks: pending.ticks + 1 };
        if (next.ticks >= POLL_MAX_TICKS) {
          resolutions.push({ kind: "cancel", pending, outcome: "timedOut", observedWorking: pending.observedWorking });
        } else cancels.set(key, next);
      }
      const modifies = new Map<string, ModifyPending>();
      for (const [key, pending] of state.modifies) {
        const next = { ...pending, ticks: pending.ticks + 1 };
        if (next.ticks >= POLL_MAX_TICKS) {
          resolutions.push({ kind: "modify", pending, outcome: "timedOut", observedWorking: pending.observedWorking });
        } else modifies.set(key, next);
      }
      return { state: { ...state, cancels, modifies }, resolutions };
    }
    case "snapshot": {
      const resolutions: Resolution[] = [];
      const cancels = new Map<string, CancelPending>();
      for (const [key, pending] of state.cancels) {
        const step = cancelStep(pending, observe(pending.order, event.data));
        if (isPending(step)) cancels.set(key, step);
        else resolutions.push({ kind: "cancel", pending, ...step });
      }
      const modifies = new Map<string, ModifyPending>();
      for (const [key, pending] of state.modifies) {
        const step = modifyStep(pending, observe(pending.order, event.data), state.cancels.has(key));
        if (isPending(step)) modifies.set(key, step);
        else resolutions.push({ kind: "modify", pending, ...step });
      }
      const refresh = event.refreshed
        ? { nextAt: event.now + REFRESH_MIN_INTERVAL_MS, backoffMs: 0 }
        : state.refresh;
      return { state: { cancels, modifies, refresh }, resolutions };
    }
  }
}

/* ── Presentation ────────────────────────────────────────────────────── */

export type ResolutionNotice = {
  type: "error" | "warning" | "success";
  message: string;
  duration?: number;
};

function modifyParts(pending: ModifyPending): string {
  const parts: string[] = [];
  if (pending.newPrice != null) parts.push(`price $${pending.newPrice.toFixed(2)}`);
  if (pending.newQuantity != null) parts.push(`qty ${pending.newQuantity}`);
  return parts.length > 0 ? ` (${parts.join(", ")})` : "";
}

/** The toast for a resolution, or null when another resolution reports it. */
export function resolutionNotice(resolution: Resolution): ResolutionNotice | null {
  const symbol = resolution.pending.order.symbol;
  if (resolution.kind === "cancel") {
    switch (resolution.outcome) {
      case "confirmedCancelled":
        return { type: "success", message: `${symbol} order cancelled` };
      case "partiallyFilledThenCancelled":
        return {
          type: "warning",
          message: `${symbol} order partially filled (${resolution.filledQuantity} of ${resolution.pending.order.totalQuantity}), rest cancelled`,
          duration: 0,
        };
      case "filledDuringCancel":
        return { type: "warning", message: `${symbol} order filled before the cancel landed`, duration: 0 };
      case "noLongerWorking":
        return {
          type: "warning",
          message: `${symbol} order is no longer working. Fills were recorded; check executions.`,
          duration: 0,
        };
      case "timedOut":
        return resolution.observedWorking
          ? { type: "error", message: `${symbol} cancellation failed. Order still open. Try cancelling again.`, duration: 0 }
          : { type: "error", message: `${symbol} cancellation not confirmed. Check open orders before cancelling again.`, duration: 0 };
    }
  }
  switch (resolution.outcome) {
    case "confirmedModified":
      return { type: "success", message: `${symbol} order confirmed${modifyParts(resolution.pending)}` };
    case "filledDuringModify":
      return { type: "warning", message: `${symbol} order filled before the modify confirmed` };
    case "noLongerWorking":
      return { type: "warning", message: `${symbol} order is no longer working. The modify was not confirmed.` };
    case "supersededByCancel":
      return null;
    case "timedOut":
      return { type: "error", message: `${symbol} modify not confirmed by IB`, duration: 0 };
  }
}
