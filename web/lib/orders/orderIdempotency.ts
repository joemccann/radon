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
 * In-memory, per Next.js worker (the app runs a single instance) — a CDN/edge
 * layer is not involved in order POSTs. Deduped responses are flagged
 * `deduplicated: true` so suppression is observable, never silent.
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

export const CONTENT_HASH_TTL_MS = 4_000;
export const CLIENT_KEY_TTL_MS = 300_000;
/**
 * Floor on how long an indeterminate outcome is retained. The abort window is
 * itself ~30s wide, so a content-hash key's 4s TTL expires before the operator
 * has finished reading the error — the very retry it must catch lands after it.
 * Retention is `max(ttlMs, this)`; an explicit client key (300s) already exceeds
 * it. An operator who has checked open orders and genuinely wants to place again
 * passes a fresh `idempotencyKey` rather than waiting this out.
 */
export const INDETERMINATE_RETENTION_MS = 60_000;
const MAX_ENTRIES = 1_000;

interface Entry {
  promise: Promise<unknown>;
  ttlMs: number;
  settledAt: number | null;
  /** Set when placement failed indeterminately — the key is then retained. */
  indeterminate: { error: unknown } | null;
}

const registry = new Map<string, Entry>();

function evict(now: number): void {
  for (const [key, entry] of registry) {
    if (entry.settledAt !== null && now - entry.settledAt > entry.ttlMs) {
      registry.delete(key);
    }
  }
  if (registry.size > MAX_ENTRIES) {
    const oldest = registry.keys().next().value;
    if (oldest !== undefined) registry.delete(oldest);
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
    const held = existing.indeterminate;
    if (held) throw new IndeterminatePlacementError(held.error, true);
    if (!settled.ok) throw settled.error;
    return { value: settled.value as T, deduplicated: true };
  }

  const entry: Entry = {
    promise: Promise.resolve(),
    ttlMs,
    settledAt: null,
    indeterminate: null,
  };
  entry.promise = (async () => {
    try {
      const value = await placement();
      entry.settledAt = Date.now();
      return value;
    } catch (err) {
      entry.settledAt = Date.now();
      if (isIndeterminatePlacementFailure(err)) {
        entry.indeterminate = { error: err };
        entry.ttlMs = Math.max(ttlMs, INDETERMINATE_RETENTION_MS);
      }
      throw err;
    }
  })();
  registry.set(key, entry);

  try {
    const value = (await entry.promise) as T;
    return { value, deduplicated: false };
  } catch (err) {
    if (entry.indeterminate) {
      // Key RETAINED: the order may be live at IB, so no retry may re-place it.
      throw new IndeterminatePlacementError(err, false);
    }
    registry.delete(key); // deterministic failure → a genuine retry must re-attempt
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

/** Test-only: clear the registry between cases. */
export function __resetOrderIdempotency(): void {
  registry.clear();
}
