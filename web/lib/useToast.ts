"use client";

import { useCallback, useRef, useState } from "react";

export type ToastType = "error" | "warning" | "success";

export type Toast = {
  id: string;
  type: ToastType;
  message: string;
  /** auto-dismiss delay in ms (default 5000, 0 = manual) */
  duration?: number;
};

let nextId = 0;

const EXIT_MS = 150;

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [exitingIds, setExitingIds] = useState<Set<string>>(new Set());
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const removeToast = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const dismissToast = useCallback((id: string) => {
    setExitingIds((prev) => new Set(prev).add(id));
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      setExitingIds((prev) => {
        const n = new Set(prev);
        n.delete(id);
        return n;
      });
    }, EXIT_MS);
  }, []);

  const addToast = useCallback(
    (type: ToastType, message: string, duration = 5000) => {
      const id = `toast-${++nextId}`;
      const toast: Toast = { id, type, message, duration };

      setToasts((prev) => [...prev, toast]);

      if (duration > 0) {
        const timer = setTimeout(() => {
          timersRef.current.delete(id);
          dismissToast(id);
        }, duration);
        timersRef.current.set(id, timer);
      }

      return id;
    },
    [dismissToast],
  );

  return { toasts, exitingIds, addToast, dismissToast, removeToast };
}
