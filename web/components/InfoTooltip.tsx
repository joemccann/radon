"use client";

import { useRef, useState } from "react";

/**
 * Inline hover tooltip — renders a small "?" circle that, on hover,
 * shows a 260px-wide explanation box. Uses position:fixed so the popup
 * escapes parent overflow:hidden/auto containers. Flips below the
 * trigger when there isn't enough viewport space above.
 */
export default function InfoTooltip({ text }: { text: string }) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  const ref = useRef<HTMLSpanElement>(null);

  function show() {
    const el = ref.current;
    if (!el) return;
    setRect(el.getBoundingClientRect());
  }

  function hide() {
    setRect(null);
  }

  // Determine whether to flip below: if trigger is within 120px of viewport top
  const flipBelow = rect ? rect.top < 120 : false;

  return (
    <span
      ref={ref}
      style={{ display: "inline-flex", alignItems: "center" }}
      onMouseEnter={show}
      onMouseLeave={hide}
    >
      <span
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
          cursor: "default",
          lineHeight: 1,
          flexShrink: 0,
        }}
      >
        ?
      </span>
      {rect && (
        <span
          style={{
            position: "fixed",
            ...(flipBelow
              ? { top: rect.bottom + 6 }
              : { top: rect.top - 6, transform: "translateY(-100%)" }),
            left: Math.max(8, Math.min(rect.left + rect.width / 2 - 130, typeof window !== "undefined" ? window.innerWidth - 268 : 1200)),
            background: "var(--bg-panel)",
            border: "1px solid var(--border-focus)",
            boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
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
