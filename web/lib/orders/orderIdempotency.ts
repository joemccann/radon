/**
 * Idempotent order placement guard.
 *
 * Dedups duplicate POST /api/orders/place calls so a UI double-click or a client
 * network retry can't place the same real-money order twice. Two protections:
 *
 *  - IN-FLIGHT: while one placement is executing (the IB subprocess takes
 *    seconds), an identical concurrent request awaits the SAME result instead of
 *    placing again. This is the primary double-click guard and is false-positive
 *    free — you cannot deliberately place a second identical order while the
 *    first is still in flight.
 *  - SHORT TTL after completion: an immediate retry within the window returns the
 *    prior result. Content-hash keys use a short TTL (a deliberate identical
 *    repeat in seconds is implausible); an explicit client key uses a long TTL.
 *
 * Per Next.js worker (the app runs a single instance) — a CDN/edge layer is not
 * involved in order POSTs. Deduped responses are flagged `deduplicated: true`
 * so suppression is observable, never silent.
 *
 * DURABILITY (REL-022 / R-043): the in-process Map is the L1 read path, but a
 * restart/deploy wipes it — exactly the window client retries straddle, so a
 * double-submit across a deploy placed twice. Settled outcomes (success or
 * indeterminate) are therefore mirrored SYNCHRONOUSLY to a JSON file
 * (`data/order_idempotency.json`, overridable via
 * `RADON_ORDER_IDEMPOTENCY_FILE`) and lazily rehydrated at first access after a
 * restart. Expired entries are pruned on load and on every write. A corrupt or
 * missing file starts empty — the durable layer must never fail an order route.
 * Deterministic failures are never persisted (their key is cleared; a genuine
 * retry must re-attempt). An IN-FLIGHT placement is persisted too, before it
 * runs: a restart mid-placement rehydrates it as an indeterminate tombstone,
 * because the dead process may already have reached IB (audit 2026-11-03 #12).
 *
 * Failure is NOT one class:
 *
 *  - DETERMINISTIC (upstream 4xx, malformed body, an explicit IB rejection): the
 *    order provably did not become live, so the key is cleared and a genuine
 *    retry re-attempts.
 *  - INDETERMINATE (the client-side abort of an in-flight placement): the request
 *    reached FastAPI and IB may already hold a live order. Clearing the key here
 *    is the T-021 double-place bug — the operator sees a failure, retries the
 *    identical order, and the retry is placed on top of a live one. So the key is
 *    RETAINED with the indeterminate outcome as its terminal answer: an identical
 *    retry inside the window gets that same answer, `deduplicated: true`, WITHOUT
 *    re-running placement.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Content-hash fallback (R-051): 15s spans a double-click, a client transport
// retry and a React remount, while a genuinely intended identical second clip
// (scaling into a position; re-placing a cancelled stop with the same payload)
// places again once it elapses. The 300s it was briefly raised to suppressed
// real intent and left the operator holding half the intended position. An
// explicit client key states user intent precisely and keeps the long window.
export const CONTENT_HASH_TTL_MS = 15_000;
export const CLIENT_KEY_TTL_MS = 300_000;
/**
 * Floor on how long an indeterminate outcome is retained. The abort window is
 * itself ~30s wide.
 * Retention is `max(ttlMs, this)`; an explicit client key (300s) already exceeds
 * it. An operator who has checked open orders and genuinely wants to place again
 * passes a fresh `idempotencyKey` rather than waiting this out.
 */
export const INDETERMINATE_RETENTION_MS = 60_000;
/**
 * Upper bound on one placement: the route's 30s radonFetch budget plus slack.
 * An in-flight record older than this belongs to a process that died
 * mid-placement; it is treated as settled indeterminate at this point.
 */
export const IN_FLIGHT_MAX_MS = 35_000;
const MAX_ENTRIES = 1_000;

/**
 * One key's lifecycle. A deterministic failure has no state: its key is
 * deleted. Only `succeeded` entries are eligible for MAX_ENTRIES eviction.
 */
type EntryState =
  | { kind: "inFlight"; startedAt: number }
  | { kind: "succeeded"; settledAt: number; value: unknown }
  | { kind: "indeterminate"; settledAt: number; error: unknown };

interface Entry {
  promise: Promise<unknown>;
  ttlMs: number;
  state: EntryState;
}

const registry = new Map<string, Entry>();
let hydratedFromDisk = false;

// ── Durable layer (L2) ────────────────────────────────────────────────

type PersistedEntry =
  | { ttlMs: number; settledAt: number; value?: unknown; indeterminate?: undefined; inFlightSince?: undefined }
  | { ttlMs: number; settledAt: number; indeterminate: { name: string; message: string }; inFlightSince?: undefined }
  | { ttlMs: number; inFlightSince: number };

interface PersistedStore {
  v: 1;
  entries: Record<string, PersistedEntry>;
}

/**
 * Per-module-instance token: under vitest, `vi.resetModules()` has always
 * meant "fresh empty idempotency state" — a durable file shared across module
 * instances would leak state between tests, so the default test path is unique
 * per instance. Restart-simulation tests pin the path explicitly via
 * `RADON_ORDER_IDEMPOTENCY_FILE` instead.
 */
const testInstanceId = `${process.pid}-${Math.random().toString(36).slice(2)}`;

function storeFilePath(): string {
  const override = process.env.RADON_ORDER_IDEMPOTENCY_FILE;
  if (override) return override;
  if (process.env.VITEST) {
    // Hermetic default under vitest: never touch the real data/ store.
    return path.join(
      os.tmpdir(),
      `radon-order-idempotency-vitest-${testInstanceId}.json`,
    );
  }
  return path.join(resolveDataDir(), "order_idempotency.json");
}

/**
 * The repo-root `data/` dir. `process.cwd()` is `web/` under the dev server and
 * the repo root under vitest, so walk up like `lib/tools/runner.ts` does.
 */
function resolveDataDir(): string {
  const candidates = [
    process.cwd(),
    path.resolve(process.cwd(), ".."),
    path.resolve(process.cwd(), "..", ".."),
  ];
  for (const candidate of candidates) {
    const dir = path.join(candidate, "data");
    if (fs.existsSync(dir)) return dir;
  }
  return path.resolve(process.cwd(), "..", "data");
}

function isLiveAt(entry: { settledAt: number; ttlMs: number }, now: number): boolean {
  return now - entry.settledAt <= entry.ttlMs;
}

/** Retention of an indeterminate outcome: never below the floor. */
function indeterminateTtl(ttlMs: number): number {
  return Math.max(ttlMs, INDETERMINATE_RETENTION_MS);
}

/**
 * When a persisted record stops guarding its key. An in-flight record from a
 * dead process counts as settled indeterminate once IN_FLIGHT_MAX_MS passed.
 */
function persistedWindow(persisted: PersistedEntry): { settledAt: number; ttlMs: number } {
  if (persisted.inFlightSince !== undefined) {
    return {
      settledAt: persisted.inFlightSince + IN_FLIGHT_MAX_MS,
      ttlMs: indeterminateTtl(persisted.ttlMs),
    };
  }
  return persisted;
}

function ensureHydrated(): void {
  if (hydratedFromDisk) return;
  hydratedFromDisk = true;
  const now = Date.now();
  for (const [key, persisted] of Object.entries(readStore().entries)) {
    if (registry.has(key) || !isLiveAt(persistedWindow(persisted), now)) continue;
    registry.set(key, rehydrateEntry(persisted));
  }
}

function readStore(): PersistedStore {
  try {
    const parsed: unknown = JSON.parse(
      fs.readFileSync(/* turbopackIgnore: true */ storeFilePath(), "utf8"),
    );
    const store = parsed as PersistedStore;
    if (store && store.v === 1 && store.entries && typeof store.entries === "object") {
      return store;
    }
  } catch {
    // Missing or corrupt file → start empty; never fail an order route.
  }
  return { v: 1, entries: {} };
}

function indeterminateEntry(cause: Error, window: { settledAt: number; ttlMs: number }): Entry {
  const promise = Promise.reject(cause);
  promise.catch(() => {}); // pre-handled: rehydrated keys may never be re-hit
  return {
    promise,
    ttlMs: window.ttlMs,
    state: { kind: "indeterminate", settledAt: window.settledAt, error: cause },
  };
}

function rehydrateEntry(persisted: PersistedEntry): Entry {
  if (persisted.inFlightSince !== undefined) {
    // The process that ran this placement died before it settled. IB may hold
    // the order, so the key becomes an indeterminate tombstone.
    const cause = Object.assign(
      new Error("Placement was in flight when the server restarted; its outcome at IB is unknown."),
      { name: "RestartedInFlight" },
    );
    return indeterminateEntry(cause, persistedWindow(persisted));
  }
  if (persisted.indeterminate) {
    const cause = Object.assign(new Error(persisted.indeterminate.message), {
      name: persisted.indeterminate.name,
    });
    return indeterminateEntry(cause, persisted);
  }
  return {
    promise: Promise.resolve(persisted.value),
    ttlMs: persisted.ttlMs,
    state: { kind: "succeeded", settledAt: persisted.settledAt, value: persisted.value },
  };
}

/**
 * Mirror every settled, unexpired outcome to disk, synchronously and
 * atomically (tmp + rename). Snapshot semantics prune expired keys on every
 * write, so the file cannot grow unboundedly. Best-effort: any fs failure is
 * swallowed — the L1 Map keeps working and the order route never crashes.
 */
function persistStore(): void {
  try {
    const now = Date.now();
    const entries: Record<string, PersistedEntry> = {};
    for (const [key, entry] of registry) {
      const persisted = serializeEntry(entry, now);
      if (persisted) entries[key] = persisted;
    }
    const file = storeFilePath();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ v: 1, entries } satisfies PersistedStore));
    fs.renameSync(tmp, file);
  } catch {
    // Durable layer is best-effort by contract.
  }
}

function serializeEntry(entry: Entry, now: number): PersistedEntry | null {
  const state = entry.state;
  switch (state.kind) {
    case "inFlight":
      return { ttlMs: entry.ttlMs, inFlightSince: state.startedAt };
    case "indeterminate": {
      if (!isLiveAt({ settledAt: state.settledAt, ttlMs: entry.ttlMs }, now)) return null;
      const error = state.error as { name?: unknown; message?: unknown };
      return {
        ttlMs: entry.ttlMs,
        settledAt: state.settledAt,
        indeterminate: {
          name: typeof error?.name === "string" ? error.name : "Error",
          message: typeof error?.message === "string" ? error.message : "",
        },
      };
    }
    case "succeeded": {
      if (!isLiveAt({ settledAt: state.settledAt, ttlMs: entry.ttlMs }, now)) return null;
      try {
        JSON.stringify(state.value);
      } catch {
        return null; // non-serializable result stays L1-only
      }
      return { ttlMs: entry.ttlMs, settledAt: state.settledAt, value: state.value };
    }
  }
}

function isExpired(entry: Entry, now: number): boolean {
  const state = entry.state;
  if (state.kind === "inFlight") return false;
  return !isLiveAt({ settledAt: state.settledAt, ttlMs: entry.ttlMs }, now);
}

function evict(now: number): void {
  for (const [key, entry] of registry) {
    if (isExpired(entry, now)) registry.delete(key);
  }
  // The cap only ever drops settled successes, oldest first: an in-flight or
  // indeterminate key is the guard against a second live order.
  for (const [key, entry] of registry) {
    if (registry.size <= MAX_ENTRIES) break;
    if (entry.state.kind === "succeeded") registry.delete(key);
  }
}

export interface IdempotentResult<T> {
  value: T;
  deduplicated: boolean;
}

/** Marker a caller stamps on an error to force the indeterminate path. */
const INDETERMINATE_PLACEMENT = Symbol.for("radon.indeterminatePlacement");

/**
 * Declare an error indeterminate: the order's fate at IB is unknown. Lets a
 * caller that owns the upstream semantics opt into key retention without this
 * module importing its error types.
 */
export function markIndeterminatePlacement<E extends object>(error: E): E {
  (error as Record<PropertyKey, unknown>)[INDETERMINATE_PLACEMENT] = true;
  return error;
}

/**
 * A failure that leaves the order's fate unknown. `radonFetch` bounds the
 * placement with `AbortSignal.timeout`, which rejects with a DOMException named
 * `TimeoutError` (an upstream/host abort surfaces as `AbortError`) — and it fires
 * AFTER the request reached FastAPI, whose own budget is shorter, so IB may
 * already hold a live order.
 *
 * Matched on `name`, not on message text: a deterministic upstream error whose
 * detail merely mentions "timeout" (an IB gateway message, say) must keep the
 * clear-the-key retry semantics. Anything else is opted in explicitly via
 * `markIndeterminatePlacement`.
 */
export function isIndeterminatePlacementFailure(error: unknown): boolean {
  if (error === null || typeof error !== "object") return false;
  if ((error as Record<PropertyKey, unknown>)[INDETERMINATE_PLACEMENT] === true) {
    return true;
  }
  const name = (error as { name?: unknown }).name;
  return name === "TimeoutError" || name === "AbortError";
}

/**
 * Placement ended indeterminate. Terminal for the retention window: the key is
 * held, so an identical retry raises this same error with `deduplicated: true`
 * instead of re-placing an order that may already be live at IB. Callers must
 * render it as an explicit "check open orders" answer, never a generic 500.
 */
export class IndeterminatePlacementError extends Error {
  constructor(
    cause: unknown,
    readonly deduplicated: boolean,
  ) {
    super(
      "Order placement outcome is indeterminate: the request was aborted before IB confirmed the order.",
      { cause },
    );
    this.name = "IndeterminatePlacementError";
  }
}

/**
 * Run `placement` under the idempotency key, deduping concurrent/just-completed
 * duplicates. The first caller runs it (`deduplicated: false`); duplicates get
 * the same resolved value (`deduplicated: true`). A deterministic rejection
 * clears the key; an indeterminate one (see `isIndeterminatePlacementFailure`)
 * retains it and every caller inside the window raises
 * `IndeterminatePlacementError`.
 */
export async function runIdempotentOrder<T>(
  key: string,
  ttlMs: number,
  placement: () => Promise<T>,
): Promise<IdempotentResult<T>> {
  ensureHydrated();
  evict(Date.now());

  const existing = registry.get(key);
  if (existing) {
    // Awaits if still in-flight; settles immediately if already done within TTL.
    const settled = await existing.promise.then(
      (value) => ({ ok: true as const, value }),
      (error: unknown) => ({ ok: false as const, error }),
    );
    // Read the outcome only after the settle: the entry records an indeterminate
    // placement before its rejection propagates.
    const held = existing.state;
    if (held.kind === "indeterminate") throw new IndeterminatePlacementError(held.error, true);
    if (!settled.ok) throw settled.error;
    return { value: settled.value as T, deduplicated: true };
  }

  const entry: Entry = {
    promise: Promise.resolve(),
    ttlMs,
    state: { kind: "inFlight", startedAt: Date.now() },
  };
  // Registered and persisted BEFORE placement runs, so a restart mid-placement
  // still guards the key.
  registry.set(key, entry);
  persistStore();
  entry.promise = (async () => {
    try {
      const value = await placement();
      entry.state = { kind: "succeeded", settledAt: Date.now(), value };
      persistStore();
      return value;
    } catch (err) {
      if (isIndeterminatePlacementFailure(err)) {
        entry.state = { kind: "indeterminate", settledAt: Date.now(), error: err };
        entry.ttlMs = indeterminateTtl(ttlMs);
      } else if (registry.get(key) === entry) {
        // Deterministic failure: a genuine retry must re-attempt.
        registry.delete(key);
      }
      persistStore();
      throw err;
    }
  })();

  try {
    const value = (await entry.promise) as T;
    return { value, deduplicated: false };
  } catch (err) {
    if (entry.state.kind === "indeterminate") {
      // Key RETAINED: the order may be live at IB, so no retry may re-place it.
      throw new IndeterminatePlacementError(err, false);
    }
    throw err;
  }
}

/** Stable content key: order-independent JSON of the placement payload. */
export function contentKey(payload: unknown): string {
  return "h:" + stableStringify(payload);
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return "[" + value.map(stableStringify).join(",") + "]";
  }
  const obj = value as Record<string, unknown>;
  return (
    "{" +
    Object.keys(obj)
      .sort()
      .map((k) => JSON.stringify(k) + ":" + stableStringify(obj[k]))
      .join(",") +
    "}"
  );
}

/** Test-only: clear the registry AND the durable file between cases. */
export function __resetOrderIdempotency(): void {
  registry.clear();
  hydratedFromDisk = false;
  try {
    fs.rmSync(storeFilePath(), { force: true });
  } catch {
    // Best-effort, mirrors the durable layer's never-crash contract.
  }
}

/**
 * Test-only: simulate a process restart/deploy — drops the in-memory Map but
 * NOT the durable file, so the next access rehydrates from disk (REL-022).
 */
export function __restartOrderIdempotencyForTests(): void {
  registry.clear();
  hydratedFromDisk = false;
}
