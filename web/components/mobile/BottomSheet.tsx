"use client";

import { useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { useDialogChrome } from "@/lib/useDialogChrome";

type BottomSheetProps = {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  /**
   * Sticky footer rendered inside the .m-sheet__footer thumb-zone.
   * Backward-compatible with callers that already pass a footer node.
   */
  footer?: ReactNode;
  /**
   * Optional isolated destructive action slot rendered below the footer
   * with a --negative tinted border-top separator.
   */
  destructive?: ReactNode;
  testId?: string;
  /**
   * Optional explicit max-height. When omitted the .m-sheet class enforces
   * 88vh; pass a value only when a caller needs a tighter ceiling.
   */
  maxHeight?: string;
};

const DRAG_DISMISS_THRESHOLD = 80;

export function BottomSheet({
  open,
  onClose,
  title,
  children,
  footer,
  destructive,
  testId,
  maxHeight,
}: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement | null>(null);
  const dragStartRef = useRef<{ y: number; offset: number } | null>(null);

  const { panelRef } = useDialogChrome<HTMLDivElement>({ open, onClose });

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
    <div className="mobile-sheet-root" ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" data-testid={testId}>
      <button
        type="button"
        className="mobile-sheet-backdrop"
        aria-label="Close"
        onClick={onClose}
      />
      <div
        ref={sheetRef}
        className="mobile-sheet m-sheet"
        style={sheetStyle}
        data-testid={testId ? `${testId}-panel` : undefined}
      >
        {/* Grip handle — drag target + visual affordance */}
        <div
          className="mobile-sheet__handle-bar"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          role="separator"
          aria-label="Drag to dismiss"
        >
          <div className="mobile-sheet__handle m-sheet__grip" aria-hidden />
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

        <div className="mobile-sheet__body m-sheet__body-scroll">{children}</div>

        {footer ? <div className="mobile-sheet__footer m-sheet__footer">{footer}</div> : null}

        {destructive ? <div className="m-sheet__destructive">{destructive}</div> : null}
      </div>
    </div>
  );
}

export default BottomSheet;
