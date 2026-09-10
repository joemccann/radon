/**
 * REL-236 / R-641 (standing NF-3): a half-open relay socket keeps painting
 * last-tick marks into the order ticket and modify modal while nothing
 * disarms submit. This gate turns quote age and (when the caller knows it)
 * feed connectivity into a submit permit.
 *
 * Semantics:
 * - `feedConnected === false` closes the gate regardless of quote age.
 * - A parseable quote timestamp older than the freshness window closes it;
 *   the reason derives the age from the timestamp itself.
 * - No quote evidence (null/empty/unparseable timestamp) leaves the gate
 *   OPEN: surfaces without a live quote keep their existing guards, and a
 *   BAG net that deliberately carries "" already renders as a closed market.
 */

/** Matches LIVE_QUOTE_MAX_AGE_MS in quoteTelemetry: past this age the
 *  telemetry block itself relabels to CLOSE, so submit must disarm too. */
export const QUOTE_SUBMIT_MAX_AGE_MS = 5 * 60 * 1000;

export type QuoteSubmitGateState = {
  open: boolean;
  reason: string | null;
};

export function quoteSubmitGate(input: {
  quoteTimestamp?: string | null;
  feedConnected?: boolean;
  nowMs?: number;
}): QuoteSubmitGateState {
  if (input.feedConnected === false) {
    return {
      open: false,
      reason: "Live feed disconnected. Submit disabled until quotes resume.",
    };
  }
  const timestamp = input.quoteTimestamp;
  if (timestamp == null || timestamp === "") return { open: true, reason: null };
  const quoteMs = Date.parse(timestamp);
  if (!Number.isFinite(quoteMs)) return { open: true, reason: null };
  const nowMs = input.nowMs ?? Date.now();
  const ageMs = nowMs - quoteMs;
  if (ageMs > QUOTE_SUBMIT_MAX_AGE_MS) {
    const ageMinutes = Math.max(1, Math.floor(ageMs / 60_000));
    return {
      open: false,
      reason: `Quote is ${ageMinutes}m old. Submit disabled until a fresh quote arrives.`,
    };
  }
  return { open: true, reason: null };
}
