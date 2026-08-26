import type { ThetaHarvesterResult } from "./types";
import { formatMonthlyExpiry, type VolConeName, type VolConeRegime } from "./volCone";

export type VolConeTone = "strong" | "warn" | "fault";

export function thetaStructLabel(result: ThetaHarvesterResult): string {
  const put = result.structure?.short_put?.strike;
  const call = result.structure?.short_call?.strike;
  if (put == null || call == null) return "—";
  return `SHORT ${put}P / ${call}C`;
}

/** Where the latest ATM IV sits inside this expiry's 90/10 cone: 0 = at or
 *  below the p10 floor (cheapest print), 1 = at or above the p90 ceiling.
 *  Null when the cone is degenerate or the ATM IV is missing. */
export function conePosition(
  name: Pick<VolConeName, "atm_iv" | "p10" | "p90">,
): number | null {
  const { atm_iv: atm, p10, p90 } = name;
  if (atm == null || p10 == null || p90 == null) return null;
  if (!Number.isFinite(atm) || !Number.isFinite(p10) || !Number.isFinite(p90)) return null;
  if (p90 <= p10) return null;
  return Math.max(0, Math.min(1, (atm - p10) / (p90 - p10)));
}

/** Score-bar fill percent, or null when the cone cannot be positioned.
 *
 *  The bar reads like every other one on this panel — longer is better — so a
 *  full bar means ATM IV is sitting on the cone floor. Returning 0 for an
 *  UNAVAILABLE cone collided with the legitimate 0 for a name at or above the
 *  p90 ceiling: a candidate whose bounds failed to compute rendered a real IV
 *  number next to an empty bar that reads as maximally rich. R-272. */
export function coneFillPct(
  name: Pick<VolConeName, "atm_iv" | "p10" | "p90">,
): number | null {
  const position = conePosition(name);
  return position == null ? null : (1 - position) * 100;
}

/** Cheap wings are the tradeable print; cheap ATM alone is the softer one. */
export function volConeTone(regime: VolConeRegime): VolConeTone {
  if (regime === "CHEAP_WINGS") return "strong";
  if (regime === "CHEAP_ATM") return "warn";
  return "fault";
}

/** Expiry cell for a hero row: "SEP 18 · 24D". */
export function volConeExpiryLabel(name: Pick<VolConeName, "expiry" | "dte">): string {
  return `${formatMonthlyExpiry(name.expiry)} · ${name.dte}D`;
}

function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** Scan sample label for the panel's calibration rail.
 *
 *  A sample from an earlier day carries its date: a bare HH:MM made a
 *  previous session's snapshot read as the current one. */
export function formatScanSample(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const sampledAt = new Date(iso);
  if (Number.isNaN(sampledAt.getTime())) return "—";
  const time = sampledAt.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  if (isSameLocalDay(sampledAt, now)) return time;
  const date = sampledAt.toLocaleDateString([], { month: "short", day: "numeric" });
  return `${date} ${time}`;
}
