"use client";

import ErrorToast from "@/components/ErrorToast";
import { useServiceHealth } from "@/lib/useServiceHealth";
import type { ServiceHealthRow } from "@/lib/useServiceHealth";
import { humanizeServiceHealthError } from "@/lib/serviceHealthError";

/** Background failures and stale telemetry notify through the global toast stack. */
export default function ServiceHealthBanner() {
  const { data, error } = useServiceHealth();

  if (error) {
    return <ErrorToast message="Service telemetry unavailable. Latest background status cannot be verified." testId="service-health-banner" />;
  }

  const degradedRows = collectDegradedRows(data?.failing ?? []);
  const degradedCount = data?.degraded_count ?? degradedRows.length;
  if (degradedCount === 0) return null;
  return <ErrorToast message={renderDegradedMessage(degradedRows)} testId="service-health-banner" />;
}

const MAX_LISTED = 3;

function collectDegradedRows(failing: ServiceHealthRow[]): ServiceHealthRow[] {
  return failing.filter((row) => row.state === "error" || row.state === "stale");
}

function renderDegradedMessage(degraded: ServiceHealthRow[]) {
  const hasError = degraded.some((row) => row.state === "error");
  const headline = hasError
    ? "Background sync degraded:"
    : "Background sync stale (no recent heartbeat):";
  const names = degraded.slice(0, MAX_LISTED).map((row) => row.service).join(", ");
  const more =
    degraded.length > MAX_LISTED ? ` +${degraded.length - MAX_LISTED} more` : "";
  const detail = resolveDetailCopy(degraded[0]);
  const fullMessage = `${headline} ${names}${more}${detail ? ` - ${detail}` : ""}`;

  return (
    <div className="service-health-banner__message" title={fullMessage}>
      <strong>{headline}</strong> {names}
      {more}
      {detail ? (
        <span className="service-health-banner__detail"> - {detail}</span>
      ) : null}
    </div>
  );
}

/**
 * Pick the safest detail copy for the first failing row.
 *
 * Preference order:
 *
 *  1. ``last_error`` (raw structured payload) run through the humanizer.
 *     This is the highest-fidelity path because the raw payload carries
 *     ``next_attempt_at`` and other metadata the API summariser strips.
 *  2. ``error_summary`` (pre-normalised plain text from the route) run
 *     through the humanizer for pattern rewriting. Used when the route
 *     ships a summary but the raw payload is unavailable.
 *  3. ``null`` when neither is present.
 *
 * The humanizer is idempotent on its own output, so step 2's pass
 * through is safe even when the input is already clean.
 */
function resolveDetailCopy(row: {
  last_error?: string | null;
  error_summary?: string | null;
} | undefined): string | null {
  if (!row) return null;
  if (row.last_error != null) {
    return humanizeServiceHealthError(row.last_error);
  }
  if (row.error_summary && row.error_summary.length > 0) {
    return humanizeServiceHealthError(row.error_summary);
  }
  return null;
}
