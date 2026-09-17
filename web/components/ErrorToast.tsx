"use client";

import { Children, isValidElement, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import ToastViewport from "@/components/ToastViewport";

type ErrorToastProps = {
  message: ReactNode;
  onRetry?: () => void;
  testId?: string;
};

function messageText(node: ReactNode): string {
  return Children.toArray(node).map(child =>
    isValidElement<{ children?: ReactNode }>(child)
      ? messageText(child.props.children)
      : String(child),
  ).join(" ");
}

function ErrorNotification({ message, onRetry, testId }: ErrorToastProps) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  return <ToastViewport>
    <div className="toast toast-error" role="alert" aria-atomic="true" data-testid={testId} style={{ alignItems: "flex-start", fontSize: 12, flexShrink: 0 }}>
      <div className="toast-message" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
        {message}
        {onRetry ? <button type="button" className="btn-secondary" style={{ marginTop: 8 }} onClick={onRetry}>Try again</button> : null}
      </div>
      <button type="button" className="toast-close" onClick={() => {
        const dialog = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"][aria-owns~="radon-toast-viewport"]')).at(-1);
        const focusTarget = dialog?.matches('[tabindex="-1"]') ? dialog : dialog?.querySelector<HTMLElement>('[tabindex="-1"]');
        focusTarget?.focus();
        setDismissed(true);
      }} aria-label="Dismiss">
        <X size={12} aria-hidden="true" />
      </button>
    </div>
  </ToastViewport>;
}

/** Persistent errors with no inline footprint. Only a new message resets dismissal. */
export default function ErrorToast(props: ErrorToastProps) {
  if (!props.message) return null;
  return <ErrorNotification key={messageText(props.message)} {...props} />;
}
