"use client";

/**
 * PaperModeToggle — switches order placement between Live (IB) and Paper
 * (shadow-fill engine). Rendered inside OrderRiskGate when the surface
 * supplies an `onPaperModeChange` handler.
 *
 * Paper mode routes the order to the homegrown shadow matcher instead of IB.
 * Note: the shadow engine cannot reproduce IB combo-router drops; for that,
 * use the IBKR native paper account (port 7497).
 */

interface PaperModeToggleProps {
  paperMode: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  className?: string;
}

export function PaperModeToggle({
  paperMode,
  onChange,
  disabled = false,
  className = "",
}: PaperModeToggleProps) {
  const label = paperMode ? "Paper" : "Live";
  const title = paperMode
    ? "Paper mode: orders route to the shadow-fill engine, not IB."
    : "Live mode: orders route to Interactive Brokers.";

  return (
    <button
      type="button"
      data-testid="paper-mode-toggle"
      className={`paper-mode-toggle ${paperMode ? "paper-mode-on" : "paper-mode-off"} ${className}`.trim()}
      disabled={disabled}
      aria-pressed={paperMode}
      title={title}
      onClick={() => onChange(!paperMode)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "2px 8px",
        borderRadius: "4px",
        fontSize: "11px",
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        cursor: disabled ? "default" : "pointer",
        border: `1px solid ${paperMode ? "var(--signal-core)" : "var(--border-subtle)"}`,
        background: paperMode
          ? "color-mix(in srgb, var(--signal-core) 16%, transparent)"
          : "transparent",
        color: paperMode ? "var(--signal-core)" : "var(--text-secondary)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: "6px",
          height: "6px",
          borderRadius: "50%",
          background: paperMode ? "var(--signal-core)" : "var(--text-secondary)",
        }}
      />
      {label}
    </button>
  );
}
