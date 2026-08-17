"use client";

import { useCallback } from "react";

export type StarToggleProps = {
  active: boolean;
  onToggle: () => void | Promise<void>;
  size?: "sm" | "md";
  label?: string;
  busy?: boolean;
};

/**
 * Reusable Terminal Native star control. Outline glyph (muted) by default;
 * fills with signal-core + a subtle glow when active. Optional mono micro-label.
 *
 * Accessible: aria-pressed reflects the active state, title gives a tooltip,
 * keyboard activation is native (button) with a signal-core focus-visible ring.
 */
export default function StarToggle({
  active,
  onToggle,
  size = "md",
  label,
  busy = false,
}: StarToggleProps) {
  const handleClick = useCallback(() => {
    if (busy) return;
    void onToggle();
  }, [busy, onToggle]);

  const glyphSize = size === "sm" ? 14 : 18;
  const title = active ? "Starred. Click to remove." : "Click to star.";

  return (
    <button
      type="button"
      data-testid="star-toggle"
      aria-pressed={active}
      title={title}
      onClick={handleClick}
      disabled={busy}
      className={`star-toggle star-toggle--${size}${active ? " star-toggle--active" : ""}${busy ? " star-toggle--busy" : ""}`}
    >
      <svg
        className="star-toggle__glyph"
        width={glyphSize}
        height={glyphSize}
        viewBox="0 0 24 24"
        fill={active ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        aria-hidden
      >
        <path d="M12 2.5l2.94 5.96 6.58.96-4.76 4.64 1.12 6.55L12 17.98 6.12 21.07l1.12-6.55L2.48 9.88l6.58-.96L12 2.5z" />
      </svg>
      {label ? <span className="star-toggle__label">{label}</span> : null}
    </button>
  );
}
