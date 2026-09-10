/** Operator-facing copy for a per-ticker flow-report failure. */

export function isSubprocessCapacityError(message: string | null | undefined): boolean {
  return message != null && /subprocess capacity exhausted/i.test(message);
}

/** A shed the server already proved persistent across its whole retry budget. */
export function isExhaustedCapacityError(message: string | null | undefined): boolean {
  return (
    isSubprocessCapacityError(message)
    && /after \d+ attempts?|retries exhausted|exhausted the retry/i.test(message ?? "")
  );
}

export function flowReportErrorCopy(message: string | null | undefined): string {
  if (isExhaustedCapacityError(message)) {
    // R-356: identical copy for a first shed and a shed the server retried
    // through told the operator to "wait a moment and refresh" for a
    // condition already proven persistent across the whole budget — and per
    // R-349 each manual refresh used to fire two more chains.
    return (
      "Scan lane stayed full through every retry. This is a capacity incident, "
      + "not a transient shed; refreshing will not clear it."
    );
  }
  if (isSubprocessCapacityError(message)) {
    return "Scan lane is full. Wait a moment and refresh.";
  }
  const retryAfter = retryAfterSecondsFrom(message);
  if (retryAfter != null) {
    return `Rate limited. Retry in ${retryAfter}s.`;
  }
  const trimmed = message?.trim();
  return trimmed || "Flow scan failed";
}

/** R-358: the route sets Retry-After on a 429 and nothing read it. */
export function retryAfterSecondsFrom(message: string | null | undefined): number | null {
  const match = /retry-after[:=\s]+(\d+)/i.exec(message ?? "");
  if (!match) return null;
  const secs = Number(match[1]);
  return Number.isFinite(secs) && secs > 0 ? secs : null;
}
