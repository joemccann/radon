"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
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
   * 88dvh; pass a value only when a caller needs a tighter ceiling. Use dvh
   * units — iOS resolves vh against the large viewport (toolbar collapsed),
   * which pushes the sticky footer below the visible area.
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

  const { panelRef, portalTarget } = useDialogChrome<HTMLDivElement>({ open, onClose });

  // The iOS keyboard shrinks only the visual viewport; this fixed root keeps
  // its layout-viewport height, so the sticky footer ends up behind the
  // keyboard with no way to scroll it into view. Track the overlap into
  // --keyboard-inset: the root pads its bottom by it (lifting the sheet) and
  // .m-sheet clamps its max-height by it.
  useEffect(() => {
    if (!open || !portalTarget) return;
    const viewport = window.visualViewport;
    const root = panelRef.current;
    if (!viewport || !root) return;
    const syncKeyboardInset = () => {
      const overlap = window.innerHeight - viewport.height - viewport.offsetTop;
      root.style.setProperty("--keyboard-inset", `${Math.max(0, Math.round(overlap))}px`);
    };
    syncKeyboardInset();
    viewport.addEventListener("resize", syncKeyboardInset);
    viewport.addEventListener("scroll", syncKeyboardInset);
    return () => {
      viewport.removeEventListener("resize", syncKeyboardInset);
      viewport.removeEventListener("scroll", syncKeyboardInset);
    };
  }, [open, portalTarget, panelRef]);

  // Portal to document.body (the NewsfeedLightbox pattern): callers render
  // this inside stacking contexts (the ticker asset-deck sits at z-index 40)
  // that would otherwise cap the sheet's z-index 90 below the tab bar's 60,
  // painting the tab bar over the sheet footer.
  if (!open || !portalTarget) return null;

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

  // Routed through --sheet-max-h (not inline max-height) so the .m-sheet rule
  // can clamp the caller's ceiling against the keyboard-adjusted viewport.
  const sheetStyle = (maxHeight ? { "--sheet-max-h": maxHeight } : {}) as React.CSSProperties;

  return createPortal(
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
    </div>,
    portalTarget,
  );
}

export default BottomSheet;
