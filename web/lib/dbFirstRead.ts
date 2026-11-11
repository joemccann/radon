/**
 * Turso-first read with a last-in-memory fallback: the single chokepoint for
 * every snapshot-backed Next.js route.
 *
 * Sources of truth are Turso and IB. Routes never read `data/*.json`. A valid,
 * non-degraded DB row is served and remembered per label; when Turso fails, times
 * out, has no row, returns a future-dated row or returns a degraded row, the last
 * good read for that label is served instead, with source "memory".
 *
 * `fresh` reports whether the SERVED read is within the route's max-age budget,
 * computed from that read's own content timestamp (scan_time / taken_at /
 * last_sync). A memory entry therefore ages like the row it came from: it can keep
 * a panel rendering through an outage, but dead data can never look current, and
 * the routes' stale gating and background rescans keep firing. Missing or
 * unparseable timestamps are infinitely stale.
 *
 * Memory is process-local and starts empty: a restart during an outage serves the
 * route's missing state, never a disk copy. Entries are shared by reference with
 * every caller, the same contract as `cachedRead`, so a route must never mutate
 * `result.data`.
 *
 * Freshness alone doesn't catch a row that's structurally present but
 * content-degraded: a writer that ran, found/produced nothing, and still wrote a
 * timestamped row (`missing: true`, an empty result set). Callers may opt in with
 * `isDegraded`: a degraded row loses to healthy memory and is never remembered;
 * with no memory it is still served. NOT on by default: several routes have a
 * LEGITIMATE empty/degraded-shaped fresh state (discover's "0 candidates today",
 * performance's insufficient_data writing series:[] on purpose so the UI shows
 * empty rather than a stale curve), so there is no universal heuristic; each call
 * site wires its own.
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
import { demoDbScope } from "./demo/demoDbIsolation";

export type TimestampedRead<T> = {
  data: T;
  /** Epoch ms of the snapshot's content timestamp; null = unknown (infinitely stale). */
  timestampMs: number | null;
};

export type DbFirstResult<T> =
  | {
      ok: true;
      source: "db" | "memory";
      data: T;
      timestampMs: number | null;
      fresh: boolean;
    }
  | { ok: false };

/**
 * Collapse a stale read onto the route's "missing" constant WITHOUT losing
 * the provenance that makes it diagnosable.
 *
 * R-125: four routes computed `fresh` and then served on `ok` alone, so a
 * writer that died on Friday kept serving Friday's print as current. Gating
 * on `fresh` fixes that, but replacing the payload with a bare constant
 * (R-194's complaint about the two routes that already gated) makes "the
 * feed died three days ago" read identically to "this job has never run".
 * The stale row's own `scan_time` rides along, plus `stale: true`.
 */
export function staleCollapse<M extends { scan_time?: string | null }, T>(
  missing: M,
  result: DbFirstResult<T>,
): M & { stale?: true } {
  if (!result.ok) return missing;
  const scanTime = (result.data as { scan_time?: unknown })?.scan_time;
  return {
    ...missing,
    stale: true as const,
    scan_time: typeof scanTime === "string" ? scanTime : missing.scan_time ?? null,
  };
}

export type DbFirstReadOptions<T> = {
  /** Latest Turso snapshot, or null when no row exists. Throwing is treated as absent. */
  fromDb: () => Promise<TimestampedRead<T> | null>;
  /** Per-route freshness budget; the served snapshot reports fresh=false beyond it. */
  maxAgeMs: number;
  /**
   * Keys the last-good memory entry and labels the warnings, so it must identify
   * the snapshot and not just the route: a per-ticker read carries the ticker.
   */
  label: string;
  /** Injectable clock for tests. */
  now?: () => number;
  /** DB read deadline. Prevents a hung read from blocking the memory fallback. */
  sourceTimeoutMs?: number;
  /**
   * Optional per-route validity gate. A DB row this predicate flags degraded
   * loses to a remembered healthy read even when it's fresher: a
   * stalled-but-newer writer must never regress the UI from real data to an
   * empty/error shape. Omitted by default: see the module docstring for why
   * there's no safe universal default. `isMissingPayload` below covers the common
   * `missing: true` / absent case; routes with their own degraded shape (e.g. an
   * empty results array that's never legitimately empty) can compose their own.
   */
  isDegraded?: (data: T) => boolean;
};

const DEFAULT_SOURCE_TIMEOUT_MS = 3_000;
const MAX_FUTURE_SKEW_MS = 60_000;
/** Static labels plus the per-ticker ones; only an idle ticker is ever evicted. */
const MAX_MEMORY_ENTRIES = 256;

const lastGoodReads = new Map<string, TimestampedRead<unknown>>();

/**
 * One process serves operator and demo principals, and a demo-scoped assistant
 * tool call makes getDb() throw DemoDbIsolationError against the prod DB. That
 * failure must not fall through to rows remembered for the operator, so the scope
 * and the database are part of the key.
 */
function memoryKey(label: string): string {
  return `${demoDbScope()}|${process.env.TURSO_DB_URL ?? ""}|${label}`;
}

function rememberRead(key: string, read: TimestampedRead<unknown>): void {
  lastGoodReads.delete(key);
  lastGoodReads.set(key, read);
  if (lastGoodReads.size > MAX_MEMORY_ENTRIES) {
    lastGoodReads.delete(lastGoodReads.keys().next().value as string);
  }
}

/** Test seam: module-level memory otherwise persists across tests in a file. */
export function __resetDbFirstReadMemoryForTests(): void {
  lastGoodReads.clear();
}

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
  const key = memoryKey(label);

  const db = await readDb(options.fromDb, label, sourceTimeoutMs);
  const nowMs = now();
  const valid = rejectFutureTimestamp(db, nowMs);
  const degraded = valid !== null && isDegraded?.(valid.data) === true;

  const served = (source: "db" | "memory", read: TimestampedRead<T>): DbFirstResult<T> => ({
    ok: true,
    source,
    data: read.data,
    timestampMs: read.timestampMs,
    fresh: isWithinMaxAge(read.timestampMs, maxAgeMs, nowMs),
  });

  if (valid && !degraded) {
    rememberRead(key, valid);
    return served("db", valid);
  }

  const memory = lastGoodReads.get(key) as TimestampedRead<T> | undefined;
  if (memory) {
    warnWithLabel(
      label,
      `${degraded ? "DB row is degraded" : "DB failed, empty or future-dated"}; serving last-good memory snapshot.`,
    );
    return served("memory", memory);
  }
  return valid ? served("db", valid) : { ok: false };
}

function rejectFutureTimestamp<T>(
  source: TimestampedRead<T> | null,
  nowMs: number,
): TimestampedRead<T> | null {
  if (source?.timestampMs != null && source.timestampMs - nowMs > MAX_FUTURE_SKEW_MS) return null;
  return source;
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

async function readDb<T>(
  read: () => Promise<TimestampedRead<T> | null>,
  label: string,
  timeoutMs: number,
): Promise<TimestampedRead<T> | null> {
  const identity: DbOperationIdentity = createDbOperationIdentity();
  // Stamp the caller's label so a teardown triggered here is attributable
  // in journald (2026-08-06: `trigger=unlabelled` was undiagnosable).
  identity.label = `dbFirstRead:${label}`;
  try {
    return (await withTimeout(
      runWithDbOperation(identity, read),
      timeoutMs,
      `DB read timed out after ${timeoutMs}ms`,
    )) ?? null;
  } catch (err) {
    // A DB timeout is the stale-pool wedge: drop the cached client so the NEXT
    // request rebuilds a fresh pool instead of waiting for the stall ceiling.
    // Memory still serves this request.
    resetDb(identity);
    warnWithLabel(label, `DB read failed: ${describeDbError(err)}`);
    return null;
  }
}

function warnWithLabel(label: string, message: string): void {
  console.warn(`[dbFirstRead:${label}] ${message}`);
}
