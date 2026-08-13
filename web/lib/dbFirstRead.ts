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
 *
 * Freshness alone doesn't catch a source that's structurally present but
 * content-degraded — a writer that ran, found/produced nothing, and still
 * wrote a timestamped row (`missing: true`, an empty result set). Without
 * a validity check, that row can beat an older, genuinely populated
 * snapshot on timestamp alone. Callers may opt in with `isDegraded`: a
 * fresher degraded source then loses to a non-degraded older one, and two
 * degraded sources keep the existing freshness order. NOT on by default —
 * several routes have a LEGITIMATE empty/degraded-shaped fresh state
 * (discover's "0 candidates today", performance's insufficient_data
 * writing series:[] on purpose so the UI shows empty rather than a stale
 * curve), so there is no universal heuristic; each call site wires its own.
 */

import { parseScanTime } from "./parseScanTime";
import { withTimeout } from "./asyncTimeout";
import { resetDb } from "./db";
import {
  createDbOperationIdentity,
  runWithDbOperation,
  type DbOperationIdentity,
} from "./dbOperation";
import { describeDbError } from "./dbExecute";

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
  /** Per-source read deadline. Prevents a hung DB read from blocking disk fallback. */
  sourceTimeoutMs?: number;
  /**
   * Optional per-route validity gate. A source whose `data` this predicate
   * flags degraded loses to a non-degraded source even when it's fresher —
   * a stalled-but-newer writer must never regress the UI from real data to
   * an empty/error shape. Two degraded sources (or two healthy ones) fall
   * through to the normal freshness order, unchanged. Omitted by default:
   * see the module docstring for why there's no safe universal default.
   * `isMissingPayload` below covers the common `missing: true` / absent
   * case; routes with their own degraded shape (e.g. an empty results
   * array that's never legitimately empty) can compose their own.
   */
  isDegraded?: (data: T) => boolean;
};

const DEFAULT_SOURCE_TIMEOUT_MS = 3_000;
const MAX_FUTURE_SKEW_MS = 60_000;

/** Epoch ms from a snapshot's content timestamp field (scan_time / taken_at / last_sync). */
export function contentTimestampMs(value: unknown): number | null {
  if (typeof value !== "string" || value.length === 0) return null;
  return parseScanTime(value)?.getTime() ?? null;
}

/**
 * Shape-agnostic degraded check: an absent payload, or one explicitly
 * flagged with the house `missing: true` marker (feedback_http_status_
 * for_real_errors — the same marker web/public/sw-decisions.js treats as
 * never cache-worthy). Safe as a shared default across every route: no
 * caller anywhere treats a missing payload or an explicit missing:true as
 * valid fresh data. Deliberately does NOT gate on empty arrays/objects —
 * that's legitimate fresh state for several routes (see DbFirstReadOptions
 * .isDegraded doc) and has to be opted into per call site instead.
 */
export function isMissingPayload(payload: unknown): boolean {
  if (payload == null) return true;
  if (typeof payload !== "object") return false;
  return (payload as { missing?: unknown }).missing === true;
}

export async function dbFirstRead<T>(
  options: DbFirstReadOptions<T>,
): Promise<DbFirstResult<T>> {
  const {
    label,
    maxAgeMs,
    now = Date.now,
    sourceTimeoutMs = DEFAULT_SOURCE_TIMEOUT_MS,
    isDegraded,
  } = options;

  const [db, disk] = await Promise.all([
    readSource(options.fromDb, "DB", label, sourceTimeoutMs),
    readSource(options.fromDisk, "disk", label, sourceTimeoutMs),
  ]);
  const nowMs = now();
  const validDb = rejectFutureTimestamp(db, nowMs);
  const validDisk = rejectFutureTimestamp(disk, nowMs);
  if (!validDb && !validDisk) return { ok: false };

  const source = pickFresherSource(validDb, validDisk, isDegraded);
  const chosen = (source === "db" ? validDb : validDisk) as TimestampedRead<T>;
  if (source === "disk") warnDiskServed(label, validDb !== null);

  return {
    ok: true,
    source,
    data: chosen.data,
    timestampMs: chosen.timestampMs,
    fresh: isWithinMaxAge(chosen.timestampMs, maxAgeMs, nowMs),
  };
}

function rejectFutureTimestamp<T>(
  source: TimestampedRead<T> | null,
  nowMs: number,
): TimestampedRead<T> | null {
  if (source?.timestampMs != null && source.timestampMs - nowMs > MAX_FUTURE_SKEW_MS) return null;
  return source;
}

function pickFresherSource<T>(
  db: TimestampedRead<T> | null,
  disk: TimestampedRead<T> | null,
  isDegraded?: (data: T) => boolean,
): "db" | "disk" {
  if (!db) return "disk";
  if (!disk) return "db";

  if (isDegraded) {
    const dbDegraded = isDegraded(db.data);
    const diskDegraded = isDegraded(disk.data);
    // Exactly one side is degraded: the healthy one wins outright, even if
    // the degraded side is the fresher of the two. Both degraded (or both
    // healthy) falls through to the ordinary freshness comparison below.
    if (dbDegraded !== diskDegraded) return dbDegraded ? "disk" : "db";
  }

  const dbTs = db.timestampMs ?? Number.NEGATIVE_INFINITY;
  const diskTs = disk.timestampMs ?? Number.NEGATIVE_INFINITY;
  return diskTs > dbTs ? "disk" : "db";
}

function isWithinMaxAge(
  timestampMs: number | null,
  maxAgeMs: number,
  nowMs: number,
): boolean {
  return timestampMs !== null
    && timestampMs - nowMs <= MAX_FUTURE_SKEW_MS
    && nowMs - timestampMs <= maxAgeMs;
}

async function readSource<T>(
  read: () => Promise<TimestampedRead<T> | null>,
  sourceName: "DB" | "disk",
  label: string | undefined,
  timeoutMs: number,
): Promise<TimestampedRead<T> | null> {
  const identity: DbOperationIdentity | undefined =
    sourceName === "DB" ? createDbOperationIdentity() : undefined;
  // Stamp the caller's label so a teardown triggered here is attributable
  // in journald (2026-08-06: `trigger=unlabelled` was undiagnosable).
  if (identity) identity.label = label ? `dbFirstRead:${label}` : "dbFirstRead";
  try {
    const pending = identity ? runWithDbOperation(identity, read) : read();
    return (await withTimeout(
      pending,
      timeoutMs,
      `${sourceName} read timed out after ${timeoutMs}ms`,
    )) ?? null;
  } catch (err) {
    // A DB-source timeout is the stale-pool wedge: drop the cached client so
    // the NEXT request rebuilds a fresh pool instead of waiting for the 12s
    // stall ceiling. The disk read still serves this request. Never reset on a
    // disk-source error — that has nothing to do with the libsql client.
    if (identity) resetDb(identity);
    warnWithLabel(label, `${sourceName} read failed: ${describeDbError(err)}`);
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
