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
 * layer is not involved in order POSTs. On failure the key is cleared so a
 * genuine retry re-attempts. Deduped responses are flagged `deduplicated: true`
 * so suppression is observable, never silent.
 */

export const CONTENT_HASH_TTL_MS = 4_000;
export const CLIENT_KEY_TTL_MS = 300_000;
const MAX_ENTRIES = 1_000;

interface Entry {
  promise: Promise<unknown>;
  ttlMs: number;
  settledAt: number | null;
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

/**
 * Run `placement` under the idempotency key, deduping concurrent/just-completed
 * duplicates. The first caller runs it (`deduplicated: false`); duplicates get
 * the same resolved value (`deduplicated: true`). A rejection clears the key.
 */
export async function runIdempotentOrder<T>(
  key: string,
  ttlMs: number,
  placement: () => Promise<T>,
): Promise<IdempotentResult<T>> {
  evict(Date.now());

  const existing = registry.get(key);
  if (existing) {
    // Awaits if still in-flight; resolves immediately if settled within TTL.
    const value = (await existing.promise) as T;
    return { value, deduplicated: true };
  }

  const entry: Entry = { promise: Promise.resolve(), ttlMs, settledAt: null };
  entry.promise = (async () => {
    const value = await placement();
    entry.settledAt = Date.now();
    return value;
  })();
  registry.set(key, entry);

  try {
    const value = (await entry.promise) as T;
    return { value, deduplicated: false };
  } catch (err) {
    registry.delete(key); // failure → a genuine retry must re-attempt
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
