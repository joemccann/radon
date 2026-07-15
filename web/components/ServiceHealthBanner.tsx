"use client";

import { AlertTriangle } from "lucide-react";
import { useServiceHealth } from "@/lib/useServiceHealth";
import type { ServiceHealthRow } from "@/lib/useServiceHealth";
import { humanizeServiceHealthError } from "@/lib/serviceHealthError";

/**
 * Service health banner — surfaces when any background dual-write or
 * scheduler is in a non-OK state. Hidden in steady state. Mounted in
 * WorkspaceShell so it appears on every page.
 *
 * Two severity tones, distinguished via ``data-severity``:
 *   - ``error`` (red): a scheduled worker raised an exception. The
 *     classic "something is broken now" signal.
 *   - ``stale`` (amber): a scheduled worker hasn't heartbeated within
 *     its freshness window. Soft signal — the process may be silent,
 *     hung, or simply between cycles on a slow cadence we
 *     miscalibrated.
 *
 * Errors take precedence over stale; both are "degraded".
 *
 * Dormant on-demand services are NOT surfaced. "Scanner has been
 * dormant for 5 days" is not an outage — the user hasn't visited
 * the scanner page, by design. Showing it constantly conditioned
 * the user to ignore the banner, masking real failures.
 *
 * The ``last_error`` column is JSON-encoded by the workers, so the
 * route handler runs ``formatServiceHealthError`` against it and ships
 * a clean ``error_summary``. The banner runs the upgraded
 * ``humanizeServiceHealthError`` on the raw payload to produce
 * banner-ready prose for the known error shapes (Flex throttle, Flex
 * auth, timeout, network blip) and falls back to the route's
 * pre-normalised summary when only that is available. Either path
 * renders plain text, never raw JSON.
 */
export default function ServiceHealthBanner() {
  const { data, error } = useServiceHealth();

  if (error) {
    return (
      <div
        className="service-health-banner"
        role="alert"
        data-testid="service-health-banner"
        data-severity="error"
      >
        <span className="service-health-banner__icon" aria-hidden>
          <AlertTriangle size={14} />
        </span>
        <div className="service-health-banner__message">
          <strong>Service telemetry unavailable.</strong> Latest background status cannot be verified.
        </div>
      </div>
    );
  }

  const degradedRows = collectDegradedRows(data?.failing ?? []);
  const degradedCount = data?.degraded_count ?? degradedRows.length;

  if (degradedCount === 0) return null;

  const severity = resolveSeverity(degradedRows);

  return (
    <div
      className="service-health-banner"
      role="alert"
      data-testid="service-health-banner"
      data-severity={severity}
    >
      <span className="service-health-banner__icon" aria-hidden>
        <AlertTriangle size={14} />
      </span>
      {renderDegradedMessage(degradedRows)}
    </div>
  );
}

type Severity = "error" | "stale";

const MAX_LISTED = 3;

function collectDegradedRows(failing: ServiceHealthRow[]): ServiceHealthRow[] {
  return failing.filter((row) => row.state === "error" || row.state === "stale");
}

function resolveSeverity(degraded: ServiceHealthRow[]): Severity {
  if (degraded.some((row) => row.state === "error")) return "error";
  return "stale";
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
