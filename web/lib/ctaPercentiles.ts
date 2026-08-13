export function normalizeCtaPercentile(value: number | null | undefined): number | null {
  if (value == null || !Number.isFinite(value)) return null;
  const normalized = value >= 0 && value < 1 ? value * 100 : value;
  return Math.max(0, Math.min(100, normalized));
}

export function formatCtaPercentileLabel(value: number | null | undefined): string {
  const normalized = normalizeCtaPercentile(value);
  if (normalized == null) return "---";
  const rounded = Math.round(normalized);
  const mod10 = rounded % 10;
  const mod100 = rounded % 100;
  const suffix = mod10 === 1 && mod100 !== 11
    ? "st"
    : mod10 === 2 && mod100 !== 12
      ? "nd"
      : mod10 === 3 && mod100 !== 13
        ? "rd"
        : "th";
  return `${rounded}${suffix}`;
}
