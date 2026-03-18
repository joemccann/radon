"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { OpenOrder, OrdersData } from "@/lib/types";
import type { ModifyOrderRequest } from "@/lib/orderModify";

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

type Notification = {
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
  drainNotifications: () => Notification[];
  setOrdersUpdater: (fn: ((data: OrdersData) => void) | null) => void;
};

const OrderActionsContext = createContext<OrderActionsContextValue | null>(null);

const POLL_INTERVAL_MS = 5_000;
const POLL_MAX_COUNT = 24; // ~2 min

export function OrderActionsProvider({ children }: { children: ReactNode }) {
  const [pendingCancels, setPendingCancels] = useState<Map<number, OpenOrder>>(new Map());
  const [pendingModifies, setPendingModifies] = useState<Map<number, PendingModify>>(new Map());
  const [cancelledOrders, setCancelledOrders] = useState<CancelledOrder[]>([]);

  const pollTimersRef = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());
  const pollCountsRef = useRef<Map<number, number>>(new Map());
  const notificationsRef = useRef<Notification[]>([]);
  const ordersUpdaterRef = useRef<((data: OrdersData) => void) | null>(null);
  const pendingModifiesRef = useRef<Map<number, PendingModify>>(new Map());

  // Keep ref in sync with state for use inside interval callbacks
  useEffect(() => {
    pendingModifiesRef.current = pendingModifies;
  }, [pendingModifies]);

  const pushNotification = useCallback((n: Notification) => {
    notificationsRef.current.push(n);
  }, []);

  /** Apply optimistic modify prices before pushing data to the UI */
  const pushOrdersData = useCallback((data: OrdersData) => {
    const currentModifies = pendingModifiesRef.current;
    if (currentModifies.size === 0) {
      ordersUpdaterRef.current?.(data);
      return;
    }
    const patched: OrdersData = {
      ...data,
      open_orders: data.open_orders.map((o) => {
        const pm = currentModifies.get(o.permId);
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

  /* ── Cancel polling ─────────────────────────────────── */

  const startCancelPoll = useCallback((order: OpenOrder) => {
    const permId = order.permId;
    pollCountsRef.current.set(permId, 0);

    const tick = async () => {
      const count = (pollCountsRef.current.get(permId) ?? 0) + 1;
      pollCountsRef.current.set(permId, count);

      try {
        const res = await fetch("/api/orders", { method: "POST" });
        if (!res.ok) {
          scheduleNext();
          return;
        }
        const data = (await res.json()) as OrdersData;

        const stillOpen = data.open_orders.some(
          (o) => o.permId === permId || (o.orderId === order.orderId && order.orderId !== 0),
        );

        if (!stillOpen) {
          pollTimersRef.current.delete(permId);
          pollCountsRef.current.delete(permId);

          setPendingCancels((prev) => {
            const next = new Map(prev);
            next.delete(permId);
            return next;
          });
          setCancelledOrders((prev) => [
            {
              permId,
              symbol: order.symbol,
              action: order.action,
              orderType: order.orderType,
              totalQuantity: order.totalQuantity,
              limitPrice: order.limitPrice,
              cancelledAt: new Date().toISOString(),
            },
            ...prev,
          ]);

          pushOrdersData(data);
          pushNotification({ type: "success", message: `${order.symbol} order cancelled` });
        } else if (count >= POLL_MAX_COUNT) {
          pollTimersRef.current.delete(permId);
          pollCountsRef.current.delete(permId);
          setPendingCancels((prev) => {
            const next = new Map(prev);
            next.delete(permId);
            return next;
          });
          pushOrdersData(data);
          pushNotification({
            type: "error",
            message: `${order.symbol} cancellation failed — order still open. Try cancelling again.`,
            duration: 0,
          });
        } else {
          pushOrdersData(data);
          scheduleNext();
        }
      } catch {
        scheduleNext();
      }
    };

    const scheduleNext = () => {
      const timer = setTimeout(tick, POLL_INTERVAL_MS);
      pollTimersRef.current.set(permId, timer);
    };

    scheduleNext();
  }, [pushNotification, pushOrdersData]);

  const requestCancel = useCallback(async (order: OpenOrder) => {
    try {
      const res = await fetch("/api/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: order.orderId, permId: order.permId }),
      });
      const json = await res.json();
      if (!res.ok) {
        pushNotification({ type: "error", message: json.error || "Cancel failed" });
      } else {
        setPendingCancels((prev) => new Map(prev).set(order.permId, order));
        startCancelPoll(order);
        if (json.orders) pushOrdersData(json.orders);
      }
    } catch {
      pushNotification({ type: "error", message: "Cancel request failed" });
    }
  }, [pushNotification, startCancelPoll, pushOrdersData]);

  /* ── Modify polling ─────────────────────────────────── */

  const startModifyPoll = useCallback((order: OpenOrder, request: ModifyOrderRequest) => {
    const permId = order.permId;
    pollCountsRef.current.set(permId, 0);

    const tick = async () => {
      const count = (pollCountsRef.current.get(permId) ?? 0) + 1;
      pollCountsRef.current.set(permId, count);

      try {
        const res = await fetch("/api/orders", { method: "POST" });
        if (!res.ok) {
          scheduleNext();
          return;
        }
        const data = (await res.json()) as OrdersData;

        const ibOrder = data.open_orders.find((o) => o.permId === permId);

        const priceConfirmed = request.newPrice == null
          || (ibOrder?.limitPrice != null && Math.abs(ibOrder.limitPrice - request.newPrice) < 0.001);
        const quantityConfirmed = request.newQuantity == null
          || ibOrder?.totalQuantity === request.newQuantity;
        const confirmed = Boolean(ibOrder && priceConfirmed && quantityConfirmed);

        if (confirmed) {
          pollTimersRef.current.delete(permId);
          pollCountsRef.current.delete(permId);

          setPendingModifies((prev) => {
            const next = new Map(prev);
            next.delete(permId);
            return next;
          });

          // Push the real data (no overlay needed — IB already shows new price)
          ordersUpdaterRef.current?.(data);
          const messageParts: string[] = [];
          if (request.newPrice != null) {
            messageParts.push(`price $${request.newPrice.toFixed(2)}`);
          }
          if (request.newQuantity != null) {
            messageParts.push(`qty ${request.newQuantity}`);
          }
          pushNotification({
            type: "success",
            message: messageParts.length > 0
              ? `${order.symbol} order confirmed (${messageParts.join(", ")})`
              : `${order.symbol} order confirmed`,
          });
        } else if (count >= POLL_MAX_COUNT) {
          pollTimersRef.current.delete(permId);
          pollCountsRef.current.delete(permId);

          setPendingModifies((prev) => {
            const next = new Map(prev);
            next.delete(permId);
            return next;
          });

          // Push fresh IB data without overlay — reverts to the real (old) price
          ordersUpdaterRef.current?.(data);
          pushNotification({
            type: "error",
            message: `${order.symbol} modify not confirmed by IB`,
            duration: 0,
          });
        } else {
          // Still pending — push data with optimistic overlay
          pushOrdersData(data);
          scheduleNext();
        }
      } catch {
        scheduleNext();
      }
    };

    const scheduleNext = () => {
      const timer = setTimeout(tick, POLL_INTERVAL_MS);
      pollTimersRef.current.set(permId, timer);
    };

    scheduleNext();
  }, [pushNotification, pushOrdersData]);

  const requestModify = useCallback(async (order: OpenOrder, request: ModifyOrderRequest) => {
    try {
      const res = await fetch("/api/orders/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: order.orderId, permId: order.permId, ...request }),
      });
      const json = await res.json();
      if (!res.ok) {
        pushNotification({ type: "error", message: json.error || "Modify failed" });
      } else {
        if (request.newPrice != null || request.newQuantity != null) {
          const pm: PendingModify = {
            order,
            newPrice: request.newPrice,
            newQuantity: request.newQuantity,
          };
          setPendingModifies((prev) => new Map(prev).set(order.permId, pm));
        }

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

        if (request.newPrice != null || request.newQuantity != null) {
          startModifyPoll(order, request);
        } else {
          pushNotification({ type: "success", message: `${order.symbol} order modified` });
        }
      }
    } catch {
      pushNotification({ type: "error", message: "Modify request failed" });
    }
  }, [pushNotification, startModifyPoll, pushOrdersData]);

  /* ── Shared infrastructure ──────────────────────────── */

  const drainNotifications = useCallback((): Notification[] => {
    if (notificationsRef.current.length === 0) return [];
    const batch = notificationsRef.current;
    notificationsRef.current = [];
    return batch;
  }, []);

  const setOrdersUpdater = useCallback((fn: ((data: OrdersData) => void) | null) => {
    ordersUpdaterRef.current = fn;
  }, []);

  // Cleanup all poll timers on unmount
  useEffect(() => {
    return () => {
      for (const timer of pollTimersRef.current.values()) {
        clearTimeout(timer);
      }
    };
  }, []);

  return (
    <OrderActionsContext.Provider
      value={{
        pendingCancels,
        pendingModifies,
        cancelledOrders,
        requestCancel,
        requestModify,
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
