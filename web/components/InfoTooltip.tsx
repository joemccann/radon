"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * Inline hover tooltip — renders a small "?" circle that, on hover,
 * shows a 260px-wide explanation box. Uses position:fixed so the popup
 * escapes parent overflow:hidden/auto containers. Flips below the
 * trigger when there isn't enough viewport space above. On coarse
 * (touch) pointers, tap toggles the popup and tap-outside dismisses it.
 */
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
  const isCoarse = useCoarsePointer();
  const isOpen = rect !== null;

  const show = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setRect(el.getBoundingClientRect());
  }, []);

  function hide() {
    setRect(null);
  }

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

  const flipBelow = (() => {
    if (!rect || popupHeight === null) return false;
    const needed = popupHeight + 14;
    if (rect.top >= needed) return false;
    const spaceBelow = (typeof window !== "undefined" ? window.innerHeight : 0) - rect.bottom;
    return spaceBelow >= needed || spaceBelow > rect.top;
  })();

  return (
    <span
      ref={ref}
      data-sort-ignore="true"
      data-testid={triggerTestId}
      style={{ display: "inline-flex", alignItems: "center" }}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
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
          style={{
            position: "fixed",
            visibility: popupHeight === null ? "hidden" : undefined,
            ...(flipBelow
              ? { top: rect.bottom + 6 }
              : { top: rect.top - 6, transform: "translateY(-100%)" }),
            left: Math.max(8, Math.min(rect.left + rect.width / 2 - 130, typeof window !== "undefined" ? window.innerWidth - 268 : 1200)),
            background: "var(--chart-tooltip-bg, var(--bg-panel))",
            border: "1px solid var(--chart-tooltip-border, var(--border-dim))",
            padding: "8px 10px",
            width: 260,
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            color: "var(--text-primary)",
            lineHeight: 1.5,
            zIndex: 9999,
            pointerEvents: "none",
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
