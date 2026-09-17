"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * Inline hover tooltip — renders a small "?" circle that, on hover,
 * shows a 260px-wide explanation box. Uses position:fixed so the popup
 * escapes parent overflow:hidden/auto containers. Flips below the
 * trigger when there isn't enough viewport space above. On coarse
 * (touch) pointers, tap toggles the popup and tap-outside dismisses it.
 *
 * The popup is hoverable and its copy selectable: leaving the trigger (or
 * the popup) only schedules a close after TOOLTIP_HIDE_DELAY_MS, so the
 * pointer can cross the gap into the text; re-entering either cancels it.
 */
export const TOOLTIP_HIDE_DELAY_MS = 300;
/** Gap between the trigger and the popup. */
const TOOLTIP_GAP = 6;
/** Breathing room kept between the popup and every viewport edge. */
const TOOLTIP_MARGIN = 8;
const TOOLTIP_WIDTH = 260;

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(value, Math.max(low, high)));
}

/**
 * The notch / status-bar inset, read from the `--safe-top` token the layout
 * already publishes. `top: 0` is not the top of the readable screen on a
 * phone: a popup placed there renders under the Dynamic Island.
 */
function safeAreaTop(): number {
  if (typeof window === "undefined" || typeof getComputedStyle !== "function") return 0;
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--safe-top");
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

type InfoTooltipProps = {
  text: string;
  ariaLabel?: string;
  triggerTestId?: string;
  contentTestId?: string;
};

function useCoarsePointer() {
  const [isCoarse, setIsCoarse] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(any-pointer: coarse)");
    setIsCoarse(query.matches);
    const onChange = (event: MediaQueryListEvent) => setIsCoarse(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return isCoarse;
}

export default function InfoTooltip({ text, ariaLabel, triggerTestId, contentTestId }: InfoTooltipProps) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [popupHeight, setPopupHeight] = useState<number | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  const popupRef = useRef<HTMLSpanElement>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isCoarse = useCoarsePointer();
  const isOpen = rect !== null;

  const cancelScheduledHide = useCallback(() => {
    if (hideTimer.current !== null) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
  }, []);

  const show = useCallback(() => {
    cancelScheduledHide();
    const el = ref.current;
    if (!el) return;
    setRect(el.getBoundingClientRect());
  }, [cancelScheduledHide]);

  const hide = useCallback(() => {
    cancelScheduledHide();
    setRect(null);
  }, [cancelScheduledHide]);

  // Leaving the trigger or the popup closes after a grace period so the
  // pointer can travel into the copy and select it.
  const scheduleHide = useCallback(() => {
    cancelScheduledHide();
    hideTimer.current = setTimeout(() => {
      hideTimer.current = null;
      setRect(null);
    }, TOOLTIP_HIDE_DELAY_MS);
  }, [cancelScheduledHide]);

  useEffect(() => cancelScheduledHide, [cancelScheduledHide]);

  function toggle() {
    if (isOpen) {
      hide();
      return;
    }
    show();
  }

  // Tap-outside (or second tap handled by toggle) dismisses on touch.
  useEffect(() => {
    if (!isOpen) return;
    function onPointerDown(event: PointerEvent) {
      const el = ref.current;
      if (el && event.target instanceof Node && el.contains(event.target)) return;
      hide();
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [isOpen]);

  // Measure the rendered popup before paint so the flip decision uses its
  // real height — a fixed threshold clips long tooltips near the top.
  useLayoutEffect(() => {
    if (!rect) {
      setPopupHeight(null);
      return;
    }
    const el = popupRef.current;
    if (el) setPopupHeight(el.getBoundingClientRect().height);
  }, [rect, text]);

  /**
   * Place the popup inside the safe viewport on both axes. Both edges are
   * clamped and the height is capped, so a long tooltip near the top of a
   * phone screen scrolls inside its box instead of running off the display.
   */
  const placement = (() => {
    if (!rect || popupHeight === null) return null;
    const viewportWidth = typeof window === "undefined" ? 1024 : window.innerWidth;
    const viewportHeight = typeof window === "undefined" ? 768 : window.innerHeight;
    const top = safeAreaTop() + TOOLTIP_MARGIN;
    const bottom = viewportHeight - TOOLTIP_MARGIN;

    const width = Math.min(TOOLTIP_WIDTH, viewportWidth - TOOLTIP_MARGIN * 2);
    const left = clamp(
      rect.left + rect.width / 2 - width / 2,
      TOOLTIP_MARGIN,
      viewportWidth - TOOLTIP_MARGIN - width,
    );

    const maxHeight = Math.max(0, bottom - top);
    const height = Math.min(popupHeight, maxHeight);
    const roomAbove = rect.top - TOOLTIP_GAP - top;
    const roomBelow = bottom - (rect.bottom + TOOLTIP_GAP);
    const below = height > roomAbove && (height <= roomBelow || roomBelow > roomAbove);
    const desired = below ? rect.bottom + TOOLTIP_GAP : rect.top - TOOLTIP_GAP - height;

    return { top: clamp(desired, top, bottom - height), left, width, maxHeight };
  })();

  return (
    <span
      ref={ref}
      data-sort-ignore="true"
      data-testid={triggerTestId}
      style={{ display: "inline-flex", alignItems: "center" }}
      onMouseEnter={show}
      onMouseLeave={scheduleHide}
      onFocus={show}
      onBlur={scheduleHide}
      aria-label={ariaLabel}
      tabIndex={0}
    >
      <button
        type="button"
        aria-expanded={isOpen}
        onClick={(event) => {
          event.stopPropagation();
          toggle();
        }}
        style={{
          appearance: "none",
          background: "transparent",
          margin: isCoarse ? -15 : 0,
          padding: isCoarse ? 15 : 0,
          minWidth: isCoarse ? 44 : undefined,
          minHeight: isCoarse ? 44 : undefined,
          border: "none",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: isCoarse ? "pointer" : "default",
          flexShrink: 0,
          color: "inherit",
          font: "inherit",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 13,
            height: 13,
            borderRadius: "50%",
            border: "1px solid var(--text-muted)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 8,
            color: "var(--text-muted)",
            lineHeight: 1,
            flexShrink: 0,
          }}
        >
          ?
        </span>
      </button>
      {rect && (
        <span
          ref={popupRef}
          data-testid={contentTestId}
          onMouseEnter={cancelScheduledHide}
          onMouseLeave={scheduleHide}
          style={{
            position: "fixed",
            visibility: placement === null ? "hidden" : undefined,
            top: placement?.top ?? rect.top,
            left: placement?.left ?? rect.left,
            maxHeight: placement?.maxHeight,
            overflowY: "auto",
            overscrollBehavior: "contain",
            background: "var(--chart-tooltip-bg, var(--bg-panel))",
            border: "1px solid var(--chart-tooltip-border, var(--border-dim))",
            padding: "8px 10px",
            width: placement?.width ?? TOOLTIP_WIDTH,
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            color: "var(--text-primary)",
            lineHeight: 1.5,
            zIndex: 9999,
            pointerEvents: "auto",
            userSelect: "text",
            cursor: "text",
            whiteSpace: "normal",
            fontWeight: 400,
            textTransform: "none",
            letterSpacing: "normal",
          }}
        >
          {text}
        </span>
      )}
    </span>
  );
}
