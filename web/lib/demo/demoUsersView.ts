// Pure view-derivations for the demo-users operator panel (Phase 6).
//
// Separated from the component so the status/countdown logic is unit-tested
// rather than discovered in the operator's browser.

import type { DemoUserRow, DemoUserStatus } from "./demoUsers";

/**
 * Effective status: an explicit `revoked` wins; otherwise a past `expires_at`
 * reads `expired` even if the row still says `active` (the sweep may not have
 * run yet). Live truth, not stored truth.
 */
export function deriveDemoStatus(
  row: Pick<DemoUserRow, "status" | "expires_at">,
  now: number = Date.now(),
): DemoUserStatus {
  if (row.status === "revoked") return "revoked";
  const expiresMs = row.expires_at ? Date.parse(row.expires_at) : NaN;
  if (Number.isFinite(expiresMs) && now >= expiresMs) return "expired";
  return "active";
}

/** "in 2d 3h" while active, "expired 5h ago" once past, "—" when unknown. */
export function formatExpiryCountdown(
  expiresAt: string | null,
  now: number = Date.now(),
): string {
  if (!expiresAt) return "—";
  const ms = Date.parse(expiresAt);
  if (!Number.isFinite(ms)) return "—";
  const deltaSec = Math.round((ms - now) / 1000);
  const past = deltaSec < 0;
  const abs = Math.abs(deltaSec);
  const days = Math.floor(abs / 86_400);
  const hours = Math.floor((abs % 86_400) / 3600);
  const mins = Math.floor((abs % 3600) / 60);

  let span: string;
  if (days > 0) span = hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  else if (hours > 0) span = mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
  else span = `${Math.max(0, mins)}m`;

  return past ? `expired ${span} ago` : `in ${span}`;
}

/** Total AI calls today across all endpoints. */
export function sumAiBurn(aiUsage: Record<string, number>): number {
  return Object.values(aiUsage).reduce((acc, n) => acc + (Number(n) || 0), 0);
}

export function statusTone(status: DemoUserStatus): "positive" | "negative" | "neutral" {
  if (status === "active") return "positive";
  if (status === "revoked") return "negative";
  return "neutral"; // expired
}
