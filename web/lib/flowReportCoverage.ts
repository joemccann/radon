import type { FlowReportData } from "@/lib/useTickerFlowReport";

/**
 * A scan that hit its history budget lands with fewer sessions than the
 * lookback; the skipped closed days backfill from the per-day cache on the
 * next scan. Empty when the window is complete.
 */
export function sessionCoverageNote(data: Pick<FlowReportData, "sessions_covered" | "history_backfill_skipped"> & { ticker?: string; lookback_days?: number }): string {
  const skipped = data.history_backfill_skipped?.length ?? 0;
  if (skipped === 0) return "";
  const covered = data.sessions_covered ?? 0;
  const tail = skipped === 1 ? "1 older session backfills" : `${skipped} older sessions backfill`;
  return `Covers ${covered} sessions. ${tail} on the next scan.`;
}
