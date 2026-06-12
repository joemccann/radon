/**
 * Freshness-aware DB-vs-disk read — the single chokepoint for every
 * snapshot-backed Next.js route (scanner, vcg, discover, flow-analysis,
 * performance).
 *
 * Both sources are read; whichever carries the NEWER content timestamp
 * is served. This protects the UI from a stalled writer on EITHER side:
 *   - Turso mirror frozen (e.g. the 2026-06-11 dual-write gate) → disk wins.
 *   - Disk fallback stale (laptop-thin mode) → DB wins.
 *
 * Timestamps are the snapshot's own content time (scan_time / taken_at /
 * last_sync), never file mtime — a stalled writer re-writing old content
 * must still read as stale. Missing/unparseable timestamps are infinitely
 * stale: a timestamped source always beats an untimestamped one, and the
 * DB wins ties (legacy DB-first order).
 *
 * `fresh` reports whether the served snapshot is within the route's
 * max-age budget. Routes gate background rescans on it: the served
 * snapshot is the fresher of the two, so "served snapshot is stale"
 * means BOTH sources are stale.
 */

import { parseScanTime } from "./parseScanTime";

export type TimestampedRead<T> = {
  data: T;
  /** Epoch ms of the snapshot's content timestamp; null = unknown (infinitely stale). */
  timestampMs: number | null;
};

export type DbFirstResult<T> =
  | {
      ok: true;
      source: "db" | "disk";
      data: T;
      timestampMs: number | null;
      fresh: boolean;
    }
  | { ok: false };

export type DbFirstReadOptions<T> = {
  /** Latest Turso snapshot, or null when no row exists. Throwing is treated as absent. */
  fromDb: () => Promise<TimestampedRead<T> | null>;
  /** On-disk JSON fallback, or null when the file is missing/empty. Throwing is treated as absent. */
  fromDisk: () => Promise<TimestampedRead<T> | null>;
  /** Per-route freshness budget; the served snapshot reports fresh=false beyond it. */
  maxAgeMs: number;
  /**
   * Optional label used in console.warn when the disk fallback fires.
   * Helps identify which route is drifting in production logs.
   */
  label?: string;
  /** Injectable clock for tests. */
  now?: () => number;
};

/** Epoch ms from a snapshot's content timestamp field (scan_time / taken_at / last_sync). */
export function contentTimestampMs(value: unknown): number | null {
  if (typeof value !== "string" || value.length === 0) return null;
  return parseScanTime(value)?.getTime() ?? null;
}

export async function dbFirstRead<T>(
  options: DbFirstReadOptions<T>,
): Promise<DbFirstResult<T>> {
  const { label, maxAgeMs, now = Date.now } = options;

  const [db, disk] = await Promise.all([
    readSource(options.fromDb, "DB", label),
    readSource(options.fromDisk, "disk", label),
  ]);
  if (!db && !disk) return { ok: false };

  const source = pickFresherSource(db, disk);
  const chosen = (source === "db" ? db : disk) as TimestampedRead<T>;
  if (source === "disk") warnDiskServed(label, db !== null);

  return {
    ok: true,
    source,
    data: chosen.data,
    timestampMs: chosen.timestampMs,
    fresh: isWithinMaxAge(chosen.timestampMs, maxAgeMs, now()),
  };
}

function pickFresherSource<T>(
  db: TimestampedRead<T> | null,
  disk: TimestampedRead<T> | null,
): "db" | "disk" {
  if (!db) return "disk";
  if (!disk) return "db";
  const dbTs = db.timestampMs ?? Number.NEGATIVE_INFINITY;
  const diskTs = disk.timestampMs ?? Number.NEGATIVE_INFINITY;
  return diskTs > dbTs ? "disk" : "db";
}

function isWithinMaxAge(
  timestampMs: number | null,
  maxAgeMs: number,
  nowMs: number,
): boolean {
  return timestampMs !== null && nowMs - timestampMs <= maxAgeMs;
}

async function readSource<T>(
  read: () => Promise<TimestampedRead<T> | null>,
  sourceName: "DB" | "disk",
  label: string | undefined,
): Promise<TimestampedRead<T> | null> {
  try {
    return (await read()) ?? null;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    warnWithLabel(label, `${sourceName} read failed: ${message}`);
    return null;
  }
}

function warnDiskServed(label: string | undefined, dbWasPresent: boolean): void {
  warnWithLabel(
    label,
    dbWasPresent
      ? "DB snapshot is staler than disk; serving disk (DB writer drift)."
      : "DB empty or failed; serving disk fallback.",
  );
}

function warnWithLabel(label: string | undefined, message: string): void {
  console.warn(`[dbFirstRead${label ? `:${label}` : ""}] ${message}`);
}
