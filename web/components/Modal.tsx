"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDialogChrome } from "@/lib/useDialogChrome";

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  className?: string;
};

const EXIT_MS = 120;

export default function Modal({ open, onClose, title, children, className }: ModalProps) {
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [mounted, setMounted] = useState(open);
  const [exiting, setExiting] = useState(false);
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (open) {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
      setMounted(true);
      setExiting(false);
      return;
    }
    if (!mounted) return;
    setExiting(true);
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const ms = reduced ? 80 : EXIT_MS;
    exitTimerRef.current = setTimeout(() => {
      setMounted(false);
      setExiting(false);
      exitTimerRef.current = null;
    }, ms);
    return () => {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
    };
  }, [open, mounted]);

  // Focus trap / scroll-lock follow the open prop; exit paint keeps the node
  // mounted briefly after open becomes false.
  const { portalTarget, panelRef } = useDialogChrome<HTMLDivElement>({
    open,
    onClose,
  });

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (exiting) return;
      if (e.target === e.currentTarget) onCloseRef.current();
    },
    [exiting],
  );

  if (!mounted || !portalTarget) return null;

  return createPortal(
    <div
      className={`modal-backdrop${exiting ? " modal-backdrop--exiting" : ""}${className ? ` ${className}` : ""}`}
      onClick={handleBackdropClick}
    >
      <div
        className="modal-content"
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="modal-header">
          <h2 className="modal-title" id={titleId}>{title}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>,
    portalTarget,
  );
}
