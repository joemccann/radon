type SigMeterProps = {
  /** 0-max scale; null renders nothing. */
  value: number | null;
  tone: "pos" | "neg" | "mut";
  max?: number;
};

/**
 * Compact inline strength meter for table cells: a token-tinted track with
 * a proportional fill, so 0-100 readings scan visually instead of only as
 * digits. Purely presentational; callers own the number formatting.
 */
export function SigMeter({ value, tone, max = 100 }: SigMeterProps) {
  if (value == null || Number.isNaN(value)) return null;
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <span className={`sig-meter sig-meter--${tone}`} data-testid="sig-meter" aria-hidden>
      <i style={{ width: `${pct}%` }} />
    </span>
  );
}
