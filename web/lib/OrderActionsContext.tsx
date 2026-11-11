"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { OpenOrder, OrdersData } from "@/lib/types";
import type { ModifyOrderRequest } from "@/lib/orderModify";
import {
  INITIAL_ORDER_ACTION_STATE,
  POLL_INTERVAL_MS,
  hasPending,
  isCancelPending,
  nextRequest,
  resolutionNotice,
  transition,
  type OrderActionEvent,
  type OrderActionState,
  type Resolution,
} from "@/lib/orders/orderActionMachine";
import { orderIdentityKey } from "@/lib/orders/orderDialogMachine";

/** Snapshot of a cancelled order for the executed table */
export type CancelledOrder = {
  permId: number;
  symbol: string;
  action: string;
  orderType: string;
  totalQuantity: number;
  limitPrice: number | null;
  cancelledAt: string; // ISO timestamp
};

export type PendingModify = {
  order: OpenOrder;
  newPrice?: number;
  newQuantity?: number;
};

type ModifyRequestWithOutsideRth = ModifyOrderRequest & { outsideRth?: boolean };

export type Notification = {
  type: "error" | "warning" | "success";
  message: string;
  duration?: number;
};

type OrderActionsContextValue = {
  pendingCancels: Map<number, OpenOrder>;
  pendingModifies: Map<number, PendingModify>;
  cancelledOrders: CancelledOrder[];
  requestCancel: (order: OpenOrder) => Promise<void>;
  requestModify: (order: OpenOrder, request: ModifyOrderRequest) => Promise<void>;
  /** Queue a toast notification (drained into the global toast system by WorkspaceShell). */
  pushNotification: (n: Notification) => void;
  drainNotifications: () => Notification[];
  setOrdersUpdater: (fn: ((data: OrdersData) => void) | null) => void;
};

const OrderActionsContext = createContext<OrderActionsContextValue | null>(null);

/** Caddy's empty 504. The broker call may already have landed. */
const MODIFY_STATUS_UNKNOWN =
  "Modify status unknown. The change may already be at IB. Check the order before sending it again.";

function isOpaqueEdgeTimeout(res: Response): boolean {
  const isJson = (res.headers.get("content-type") ?? "").includes("application/json");
  return !isJson && (res.status === 502 || res.status === 504);
}

function retryAfterMs(res: Response): number | undefined {
  const seconds = Number(res.headers.get("Retry-After"));
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1_000 : undefined;
}

export function OrderActionsProvider({ children }: { children: ReactNode }) {
  // One explicit machine (lib/orders/orderActionMachine.ts) holds every
  // pending cancel / modify. The ref is the source of truth for the poller;
  // the state copy only drives renders.
  const machineRef = useRef<OrderActionState>(INITIAL_ORDER_ACTION_STATE);
  const [machine, setMachine] = useState<OrderActionState>(INITIAL_ORDER_ACTION_STATE);
  const [cancelledOrders, setCancelledOrders] = useState<CancelledOrder[]>([]);

  /** The single shared poll timer, and whether a poll request is in flight. */
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollInFlightRef = useRef(false);
  const unmountedRef = useRef(false);
  /** Order identity keys whose cancel POST is on the wire (double-click guard). */
  const cancelRequestsInFlightRef = useRef<Set<string>>(new Set());
  const notificationsRef = useRef<Notification[]>([]);
  const ordersUpdaterRef = useRef<((data: OrdersData) => void) | null>(null);

  const pendingCancels = useMemo(
    () => new Map([...machine.cancels.values()].map((entry) => [entry.order.permId, entry.order])),
    [machine.cancels],
  );
  const pendingModifies = useMemo(
    () => new Map<number, PendingModify>([...machine.modifies.values()].map((entry) => [
      entry.order.permId,
      { order: entry.order, newPrice: entry.newPrice, newQuantity: entry.newQuantity },
    ])),
    [machine.modifies],
  );

  const pushNotification = useCallback((n: Notification) => {
    notificationsRef.current.push(n);
  }, []);

  /** Apply optimistic modify prices before pushing data to the UI */
  const pushOrdersData = useCallback((data: OrdersData) => {
    const currentModifies = machineRef.current.modifies;
    if (currentModifies.size === 0) {
      ordersUpdaterRef.current?.(data);
      return;
    }
    const patched: OrdersData = {
      ...data,
      open_orders: data.open_orders.map((o) => {
        const pm = currentModifies.get(orderIdentityKey(o));
        if (!pm) return o;
        return {
          ...o,
          limitPrice: pm.newPrice ?? o.limitPrice,
          totalQuantity: pm.newQuantity ?? o.totalQuantity,
        };
      }),
    };
    ordersUpdaterRef.current?.(patched);
  }, []);

  /* ── Confirmation machine + shared poller ───────────── */

  const dispatch = useCallback((event: OrderActionEvent): Resolution[] => {
    const { state, resolutions } = transition(machineRef.current, event);
    machineRef.current = state;
    setMachine(state);
    return resolutions;
  }, []);

  const applyResolutions = useCallback((resolutions: Resolution[]) => {
    const rows: CancelledOrder[] = [];
    for (const resolution of resolutions) {
      const notice = resolutionNotice(resolution);
      if (notice) pushNotification(notice);
      if (resolution.kind !== "cancel") continue;
      const unfilled =
        resolution.outcome === "confirmedCancelled"
          ? resolution.pending.order.totalQuantity
          : resolution.outcome === "partiallyFilledThenCancelled"
            ? resolution.pending.order.totalQuantity - resolution.filledQuantity
            : 0;
      if (unfilled <= 0) continue;
      const o = resolution.pending.order;
      rows.push({
        permId: o.permId,
        symbol: o.symbol,
        action: o.action,
        orderType: o.orderType,
        totalQuantity: unfilled,
        limitPrice: o.limitPrice,
        cancelledAt: new Date().toISOString(),
      });
    }
    if (rows.length > 0) setCancelledOrders((prev) => [...rows, ...prev]);
  }, [pushNotification]);

  const pollTickRef = useRef<() => Promise<void>>(async () => {});

  /** Arm the single poll timer when something is pending and none is armed. */
  const ensurePolling = useCallback(() => {
    if (unmountedRef.current || pollTimerRef.current || pollInFlightRef.current) return;
    if (!hasPending(machineRef.current)) return;
    pollTimerRef.current = setTimeout(() => {
      pollTimerRef.current = null;
      void pollTickRef.current();
    }, POLL_INTERVAL_MS);
  }, []);

  const pollTick = useCallback(async () => {
    const request = nextRequest(machineRef.current, Date.now());
    if (!request) return;
    pollInFlightRef.current = true;
    try {
      const res = await fetch(
        "/api/orders",
        request === "refresh" ? { method: "POST", cache: "no-store" } : { cache: "no-store" },
      );
      if (unmountedRef.current) return;
      if (res.status === 429) {
        dispatch({ type: "rateLimited", now: Date.now(), retryAfterMs: retryAfterMs(res) });
        return;
      }
      if (!res.ok) {
        applyResolutions(dispatch({ type: "pollFailed" }));
        return;
      }
      const data = (await res.json()) as OrdersData;
      if (unmountedRef.current) return;
      const resolutions = dispatch({
        type: "snapshot",
        data,
        now: Date.now(),
        refreshed: request === "refresh",
      });
      // Overlay only the modifies still pending; a resolved one shows IB's row.
      pushOrdersData(data);
      applyResolutions(resolutions);
    } catch {
      if (!unmountedRef.current) applyResolutions(dispatch({ type: "pollFailed" }));
    } finally {
      pollInFlightRef.current = false;
      ensurePolling();
    }
  }, [applyResolutions, dispatch, ensurePolling, pushOrdersData]);
  pollTickRef.current = pollTick;

  const requestCancel = useCallback(async (order: OpenOrder) => {
    const permId = order.permId;
    // One cancel per order (permId, else orderId while IB has not assigned
    // one): a second click while one is on the wire or awaiting
    // confirmation sends nothing.
    const key = orderIdentityKey(order);
    if (isCancelPending(machineRef.current, order) || cancelRequestsInFlightRef.current.has(key)) return;
    cancelRequestsInFlightRef.current.add(key);
    try {
      const res = await fetch("/api/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: order.orderId, permId }),
      });
      const json = await res.json();
      if (!res.ok) {
        pushNotification({ type: "error", message: json.error || "Cancel failed" });
        if (json.orders) pushOrdersData(json.orders);
      } else {
        dispatch({ type: "cancelRequested", order });
        ensurePolling();
        if (json.orders) pushOrdersData(json.orders);
      }
    } catch {
      pushNotification({ type: "error", message: "Cancel request failed" });
    } finally {
      cancelRequestsInFlightRef.current.delete(key);
    }
  }, [dispatch, ensurePolling, pushNotification, pushOrdersData]);

  const startModifyPoll = useCallback((order: OpenOrder, request: ModifyRequestWithOutsideRth) => {
    dispatch({
      type: "modifyRequested",
      order,
      newPrice: request.newPrice,
      newQuantity: request.newQuantity,
    });
    ensurePolling();
  }, [dispatch, ensurePolling]);

  const requestModify = useCallback(async (order: OpenOrder, request: ModifyRequestWithOutsideRth) => {
    try {
      const { outsideRth, ...requestBody } = request;
      const res = await fetch("/api/orders/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: order.orderId, permId: order.permId, ...requestBody, outsideRth }),
      });
      if (isOpaqueEdgeTimeout(res)) {
        pushNotification({ type: "warning", message: MODIFY_STATUS_UNKNOWN, duration: 0 });
        if (!request.replaceOrder && (request.newPrice != null || request.newQuantity != null)) {
          startModifyPoll(order, request);
        }
        return;
      }
      const json = await res.json();
      if (!res.ok) {
        pushNotification({ type: "error", message: json.error || "Modify failed" });
        if (json.orders) pushOrdersData(json.orders);
      } else {
        const confirmable = !request.replaceOrder && (request.newPrice != null || request.newQuantity != null);
        // Register first so the pushed snapshot carries the optimistic overlay.
        if (confirmable) startModifyPoll(order, request);

        if (json.orders) {
          if (request.replaceOrder) {
            ordersUpdaterRef.current?.(json.orders);
          } else {
            pushOrdersData(json.orders);
          }
        }

        if (request.replaceOrder) {
          pushNotification({ type: "success", message: `${order.symbol} order replaced` });
          return;
        }

        if (!confirmable) {
          pushNotification({ type: "success", message: `${order.symbol} order modified` });
        }
      }
    } catch {
      pushNotification({ type: "error", message: "Modify request failed" });
    }
  }, [pushNotification, startModifyPoll, pushOrdersData]);

  const drainNotifications = useCallback((): Notification[] => {
    if (notificationsRef.current.length === 0) return [];
    const batch = notificationsRef.current;
    notificationsRef.current = [];
    return batch;
  }, []);

  const setOrdersUpdater = useCallback((fn: ((data: OrdersData) => void) | null) => {
    ordersUpdaterRef.current = fn;
  }, []);

  // One timer to clear. An in-flight poll sees `unmountedRef` and stops.
  // Remount (StrictMode) resumes polling anything still pending.
  useEffect(() => {
    unmountedRef.current = false;
    ensurePolling();
    return () => {
      unmountedRef.current = true;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    };
  }, [ensurePolling]);

  return (
    <OrderActionsContext.Provider
      value={{
        pendingCancels,
        pendingModifies,
        cancelledOrders,
        requestCancel,
        requestModify,
        pushNotification,
        drainNotifications,
        setOrdersUpdater,
      }}
    >
      {children}
    </OrderActionsContext.Provider>
  );
}

export function useOrderActions(): OrderActionsContextValue {
  const ctx = useContext(OrderActionsContext);
  if (!ctx) throw new Error("useOrderActions must be used within OrderActionsProvider");
  return ctx;
}

/**
 * Non-throwing variant for surfaces that only need best-effort access (e.g. an
 * order form that wants to push a success toast). Returns null when rendered
 * outside the provider — production always has it via WorkspaceShell, but unit
 * tests that render a form in isolation should not be forced to wrap it.
 */
export function useOrderActionsOptional(): OrderActionsContextValue | null {
  return useContext(OrderActionsContext);
}
