/**
 * `last_sync` is when the book was last confirmed, not the newest fill.
 * An empty working book has no `open_orders.updated_at`, so the fill time
 * aged forever and /orders painted STALE after a healthy sync. The
 * orders-sync ok heartbeat is that confirmation. A warn or error row
 * stamps finished_at without rewriting the book, so only state `ok`
 * counts. A timestamp with no zone is not an instant.
 */

const ABSOLUTE_TIMESTAMP = /(?:Z|[+-]\d{2}:\d{2})$/;

export function absoluteTimestamp(value: string | null | undefined): string | null {
  if (!value || !ABSOLUTE_TIMESTAMP.test(value)) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : value;
}

export function resolveOrdersLastSync(input: {
  healthState: string | null;
  healthFinishedAt: string | null;
  latestOpenUpdatedAt: string;
  latestFillTime: string;
}): string {
  const openAt = absoluteTimestamp(input.latestOpenUpdatedAt);
  const syncedAt = input.healthState === "ok"
    ? absoluteTimestamp(input.healthFinishedAt)
    : null;
  const candidates = [syncedAt, openAt].filter((value): value is string => value != null);
  if (candidates.length === 0) return input.latestFillTime;
  return candidates.reduce((best, current) => (
    Date.parse(current) > Date.parse(best) ? current : best
  ));
}
