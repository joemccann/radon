"use client";

import { X } from "lucide-react";
import type { Toast } from "@/lib/useToast";

type ToastContainerProps = {
  toasts: Toast[];
  onDismiss: (id: string) => void;
};

export default function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.type}`}>
          <span className="toast-message">{toast.message}</span>
          <button className="toast-close" onClick={() => onDismiss(toast.id)} aria-label="Dismiss">
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}
