"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { OpenOrder, OrdersData } from "@/lib/types";

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
  newPrice: number;
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
  requestModify: (order: OpenOrder, newPrice: number) => Promise<void>;
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
        return pm ? { ...o, limitPrice: pm.newPrice } : o;
      }),
    };
    ordersUpdaterRef.current?.(patched);
  }, []);

  /* ── Cancel polling ─────────────────────────────────── */

  const startCancelPoll = useCallback((order: OpenOrder) => {
    const permId = order.permId;
    pollCountsRef.current.set(permId, 0);

    const interval = setInterval(async () => {
      const count = (pollCountsRef.current.get(permId) ?? 0) + 1;
      pollCountsRef.current.set(permId, count);

      try {
        const res = await fetch("/api/orders", { method: "POST" });
        if (!res.ok) return;
        const data = (await res.json()) as OrdersData;

        const stillOpen = data.open_orders.some(
          (o) => o.permId === permId || (o.orderId === order.orderId && order.orderId !== 0),
        );

        if (!stillOpen) {
          clearInterval(interval);
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
          clearInterval(interval);
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
        }
      } catch {
        // Network error, keep polling
      }
    }, POLL_INTERVAL_MS);

    pollTimersRef.current.set(permId, interval);
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

  const startModifyPoll = useCallback((order: OpenOrder, newPrice: number) => {
    const permId = order.permId;
    pollCountsRef.current.set(permId, 0);

    const interval = setInterval(async () => {
      const count = (pollCountsRef.current.get(permId) ?? 0) + 1;
      pollCountsRef.current.set(permId, count);

      try {
        const res = await fetch("/api/orders", { method: "POST" });
        if (!res.ok) return;
        const data = (await res.json()) as OrdersData;

        const ibOrder = data.open_orders.find((o) => o.permId === permId);

        // Check if IB now shows the new price (within $0.001 tolerance)
        const confirmed = ibOrder && ibOrder.limitPrice != null && Math.abs(ibOrder.limitPrice - newPrice) < 0.001;

        if (confirmed) {
          clearInterval(interval);
          pollTimersRef.current.delete(permId);
          pollCountsRef.current.delete(permId);

          setPendingModifies((prev) => {
            const next = new Map(prev);
            next.delete(permId);
            return next;
          });

          // Push the real data (no overlay needed — IB already shows new price)
          ordersUpdaterRef.current?.(data);
          pushNotification({
            type: "success",
            message: `${order.symbol} order confirmed at $${newPrice.toFixed(2)}`,
          });
        } else if (count >= POLL_MAX_COUNT) {
          clearInterval(interval);
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
            message: `${order.symbol} modify not confirmed by IB — price may not have changed`,
            duration: 0,
          });
        } else {
          // Still pending — push data with optimistic overlay
          pushOrdersData(data);
        }
      } catch {
        // Network error, keep polling
      }
    }, POLL_INTERVAL_MS);

    pollTimersRef.current.set(permId, interval);
  }, [pushNotification, pushOrdersData]);

  const requestModify = useCallback(async (order: OpenOrder, newPrice: number) => {
    try {
      const res = await fetch("/api/orders/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: order.orderId, permId: order.permId, newPrice }),
      });
      const json = await res.json();
      if (!res.ok) {
        pushNotification({ type: "error", message: json.error || "Modify failed" });
      } else {
        // Optimistically update the limit price in the UI
        const pm: PendingModify = { order, newPrice };
        setPendingModifies((prev) => new Map(prev).set(order.permId, pm));

        // Push optimistic data immediately
        if (json.orders) {
          pushOrdersData(json.orders);
        }

        startModifyPoll(order, newPrice);
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

  // Cleanup all poll intervals on unmount
  useEffect(() => {
    return () => {
      for (const timer of pollTimersRef.current.values()) {
        clearInterval(timer);
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
