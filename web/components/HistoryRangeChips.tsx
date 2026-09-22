"use client";

import { RANGE_PRESETS, type RangePreset, type RangePresetSlug } from "@/lib/historyRange";

interface HistoryRangeChipsProps {
  /** Currently active preset slug, or "custom" when a brush selection drives the view. */
  presets?: ReadonlyArray<RangePreset>;
  active: RangePresetSlug | "custom";
  /** Fired when the user picks a different preset. */
  onChange: (slug: RangePresetSlug) => void;
  /** Hides chips whose preset would span more sessions than this. */
  maxSessions?: number;
  /** Optional aria-label override; defaults to "Chart range". */
  ariaLabel?: string;
  /** Optional className for the wrapping nav. */
  className?: string;
  /** Test id passthrough. */
  dataTestId?: string;
}

export default function HistoryRangeChips({
  presets = RANGE_PRESETS,
  active,
  onChange,
  maxSessions,
  ariaLabel = "Chart range",
  className,
  dataTestId,
}: HistoryRangeChipsProps) {
  const visible = presets.filter((preset) => {
    if (preset.slug === "all") return true;
    if (maxSessions == null) return true;
    // Hide presets that don't change the view (e.g. "1Y" on 30 sessions).
    return preset.sessions <= maxSessions;
  });

  // Single-preset visibility is no UI — show nothing.
  if (visible.length <= 1) return null;

  return (
    <nav
      className={`history-range-chips${className ? ` ${className}` : ""}`}
      aria-label={ariaLabel}
      data-testid={dataTestId}
    >
      {visible.map((preset) => {
        const isActive = preset.slug === active;
        return (
          <button
            key={preset.slug}
            type="button"
            className={`history-range-chip${isActive ? " is-active" : ""}`}
            aria-pressed={isActive}
            onClick={() => onChange(preset.slug)}
            data-testid={dataTestId ? `${dataTestId}-${preset.slug}` : undefined}
          >
            {preset.label}
          </button>
        );
      })}
      {active === "custom" ? (
        <span
          className="history-range-chip is-active history-range-chip-custom"
          aria-label="Custom range from brush selection"
          data-testid={dataTestId ? `${dataTestId}-custom` : undefined}
        >
          Custom
        </span>
      ) : null}
    </nav>
  );
}
