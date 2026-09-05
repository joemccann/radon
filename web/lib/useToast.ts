"use client";

import { useCallback, useRef, useState } from "react";

export type ToastType = "error" | "warning" | "success";

export type Toast = {
  id: string;
  type: ToastType;
  message: string;
  /** auto-dismiss delay in ms (default 5000, 0 = manual) */
  duration?: number;
  /** coalescing identity — a later upsert with the same key updates in place */
  key?: string;
};

let nextId = 0;

const EXIT_MS = 150;

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [exitingIds, setExitingIds] = useState<Set<string>>(new Set());
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  // Live toast id per coalescing key. An entry is dropped the moment its toast
  // starts leaving, so the next upsert opens a fresh toast instead of writing
  // into one the operator already dismissed.
  const keyedIdsRef = useRef<Map<string, string>>(new Map());

  const forgetKey = useCallback((id: string) => {
    for (const [key, keyedId] of keyedIdsRef.current) {
      if (keyedId === id) keyedIdsRef.current.delete(key);
    }
  }, []);

  const removeToast = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    forgetKey(id);
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, [forgetKey]);

  const dismissToast = useCallback((id: string) => {
    forgetKey(id);
    setExitingIds((prev) => new Set(prev).add(id));
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      setExitingIds((prev) => {
        const n = new Set(prev);
        n.delete(id);
        return n;
      });
    }, EXIT_MS);
  }, [forgetKey]);

  const scheduleDismiss = useCallback(
    (id: string, duration: number) => {
      const existing = timersRef.current.get(id);
      if (existing) clearTimeout(existing);
      timersRef.current.delete(id);
      if (duration <= 0) return;
      const timer = setTimeout(() => {
        timersRef.current.delete(id);
        dismissToast(id);
      }, duration);
      timersRef.current.set(id, timer);
    },
    [dismissToast],
  );

  const addToast = useCallback(
    (type: ToastType, message: string, duration = 5000, key?: string) => {
      const id = `toast-${++nextId}`;
      setToasts((prev) => [...prev, { id, type, message, duration, key }]);
      if (key) keyedIdsRef.current.set(key, id);
      scheduleDismiss(id, duration);
      return id;
    },
    [scheduleDismiss],
  );

  /**
   * Raise a toast for `key`, or rewrite the one already on screen for it. A
   * sequence of partial fills for one order therefore reads as a single toast
   * whose quantity and price climb, not as a stack of near-identical rows.
   */
  const upsertToast = useCallback(
    (key: string, type: ToastType, message: string, duration = 5000) => {
      const liveId = keyedIdsRef.current.get(key);
      if (!liveId) return addToast(type, message, duration, key);
      setToasts((prev) =>
        prev.map((t) => (t.id === liveId ? { ...t, type, message, duration } : t)),
      );
      scheduleDismiss(liveId, duration);
      return liveId;
    },
    [addToast, scheduleDismiss],
  );

  /** True while the toast for `key` is still on screen (not dismissed). */
  const hasToastKey = useCallback((key: string) => keyedIdsRef.current.has(key), []);

  return { toasts, exitingIds, addToast, upsertToast, dismissToast, removeToast, hasToastKey };
}
