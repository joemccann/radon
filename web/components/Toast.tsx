"use client";

import { X } from "lucide-react";
import type { Toast } from "@/lib/useToast";

type ToastContainerProps = {
  toasts: Toast[];
  exitingIds: Set<string>;
  onDismiss: (id: string) => void;
};

export default function ToastContainer({ toasts, exitingIds, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`toast toast-${toast.type}${exitingIds.has(toast.id) ? " toast--exiting" : ""}`}
        >
          <span className="toast-message">{toast.message}</span>
          <button className="toast-close" onClick={() => onDismiss(toast.id)} aria-label="Dismiss">
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}
