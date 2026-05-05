"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

type BottomSheetProps = {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  testId?: string;
  /**
   * Optional explicit max-height, falls back to "90dvh".
   */
  maxHeight?: string;
};

const DRAG_DISMISS_THRESHOLD = 80;

export function BottomSheet({ open, onClose, title, children, footer, testId, maxHeight }: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement | null>(null);
  const dragStartRef = useRef<{ y: number; offset: number } | null>(null);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", handleKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!sheetRef.current) return;
    sheetRef.current.setPointerCapture(event.pointerId);
    dragStartRef.current = { y: event.clientY, offset: 0 };
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStartRef.current || !sheetRef.current) return;
    const dy = event.clientY - dragStartRef.current.y;
    const offset = Math.max(0, dy);
    dragStartRef.current.offset = offset;
    sheetRef.current.style.transform = `translateY(${offset}px)`;
  };

  const handlePointerUp = () => {
    if (!dragStartRef.current || !sheetRef.current) return;
    const offset = dragStartRef.current.offset;
    sheetRef.current.style.transform = "";
    dragStartRef.current = null;
    if (offset > DRAG_DISMISS_THRESHOLD) onClose();
  };

  const sheetStyle: React.CSSProperties = maxHeight ? { maxHeight } : {};

  return (
    <div className="mobile-sheet-root" role="dialog" aria-modal="true" data-testid={testId}>
      <button
        type="button"
        className="mobile-sheet-backdrop"
        aria-label="Close"
        onClick={onClose}
      />
      <div
        ref={sheetRef}
        className="mobile-sheet"
        style={sheetStyle}
        data-testid={testId ? `${testId}-panel` : undefined}
      >
        <div
          className="mobile-sheet__handle-bar"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          role="separator"
          aria-label="Drag to dismiss"
        >
          <div className="mobile-sheet__handle" aria-hidden />
        </div>

        {title || true ? (
          <div className="mobile-sheet__header">
            <span className="mobile-sheet__title">{title}</span>
            <button
              type="button"
              className="mobile-sheet__close"
              onClick={onClose}
              aria-label="Close"
              data-testid={testId ? `${testId}-close` : undefined}
            >
              <X size={20} aria-hidden />
            </button>
          </div>
        ) : null}

        <div className="mobile-sheet__body">{children}</div>

        {footer ? <div className="mobile-sheet__footer">{footer}</div> : null}
      </div>
    </div>
  );
}

export default BottomSheet;
