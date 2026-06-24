/**
 * Money formatters — consolidates the duplicated dollar/percent helpers that
 * previously lived inline in PnlBreakdownModal, ExposureBreakdownModal, and
 * PortfolioSnapshotCard. Each preserves its original sign and abbreviation
 * convention exactly so visual output is unchanged.
 */

const ABBREVIATION_THRESHOLD = 1_000_000;

/** Signed dollar value with sign ahead of the dollar sign: "+$1,234.00" / "-$1,234.00". */
export function fmtSigned(value: number, decimals = 0): string {
  const abs = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return `${value >= 0 ? "+" : "-"}$${abs}`;
}

/** Percentage with explicit plus on non-negative, one decimal: "+12.3%" / "-5.6%". */
export function fmtPct(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

/** Rounded USD with millions abbreviation: "$1.23M" / "$45,678" / "-$1,234". */
export function fmtUsd(value: number): string {
  if (Math.abs(value) >= ABBREVIATION_THRESHOLD) return `$${(value / ABBREVIATION_THRESHOLD).toFixed(2)}M`;
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

/** Signed rounded USD with sign ahead of the dollar sign: "+$45,678" / "-$1,234". */
export function fmtSignedUsd(value: number): string {
  return `${value >= 0 ? "+" : "-"}${fmtUsd(Math.abs(value))}`;
}

/**
 * Rounded money for dashboard summaries. Uses the typographic minus sign and an
 * em-dash placeholder for nullish input. Millions abbreviation.
 */
export function fmtMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (abs >= ABBREVIATION_THRESHOLD) return `${sign}$${(abs / ABBREVIATION_THRESHOLD).toFixed(2)}M`;
  return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

/**
 * Signed rounded money for dashboard summaries. Typographic minus sign, "$0"
 * for an exact zero, em-dash placeholder for nullish input. Millions abbreviation.
 */
export function fmtMoneySigned(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (value === 0) return `$0`;
  const sign = value < 0 ? "−" : "+";
  if (abs >= ABBREVIATION_THRESHOLD) return `${sign}$${(abs / ABBREVIATION_THRESHOLD).toFixed(2)}M`;
  return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}
