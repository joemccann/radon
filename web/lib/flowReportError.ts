/** Operator-facing copy for a per-ticker flow-report failure. */

export function isSubprocessCapacityError(message: string | null | undefined): boolean {
  return Boolean(message) && /subprocess capacity exhausted/i.test(message);
}

export function flowReportErrorCopy(message: string | null | undefined): string {
  if (isSubprocessCapacityError(message)) {
    return "Scan lane is full. Wait a moment and refresh.";
  }
  const trimmed = message?.trim();
  return trimmed || "Flow scan failed";
}
