import { classifyUpstreamFailure } from "@/lib/upstreamFailure";

export const SYNC_PENDING_WARNING = "IB sync still running - serving latest Turso snapshot";
export const SYNC_FAILED_WARNING = "IB sync failed - serving latest Turso snapshot";

/**
 * Label a producer-sync response that serves the Turso snapshot instead of
 * the sync result. A wait that elapsed is not a failure: FastAPI keeps the
 * shielded sync running and publishes it to Turso, so the client should poll
 * (X-Sync-Pending) rather than report an error.
 */
export function markSyncFallback<T extends Response>(response: T, syncError: unknown): T {
  const isPending = classifyUpstreamFailure(syncError) === "timeout";
  response.headers.set("X-Sync-Warning", isPending ? SYNC_PENDING_WARNING : SYNC_FAILED_WARNING);
  if (isPending) response.headers.set("X-Sync-Pending", "1");
  return response;
}
