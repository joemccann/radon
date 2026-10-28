/**
 * Per-client input bounds for the realtime relay. A single authenticated
 * client must not be able to make the relay allocate or queue without limit.
 */

/** Largest accepted WebSocket frame (ws defaults to 100 MiB). */
export const MAX_WS_PAYLOAD_BYTES = 1024 * 1024;

/** Most symbols / contracts / indexes honoured from one message. */
export const MAX_ITEMS_PER_MESSAGE = 1000;

/** R-036 / REL-021b: bound one client's distinct streaming subjects.
 * A browser tab is ONE client carrying every page's subjects (portfolio,
 * orders, watchlist, regime, futures, legs and the open chain; the
 * all-strikes chain alone streams up to 202 contracts), so the cap stops
 * unbounded growth without refusing a legitimate workspace.
 */
export const MAX_CLIENT_SUBSCRIPTIONS = 512;

/** Most snapshot requests waiting on the IB pacing limiter. */
export const MAX_SNAPSHOT_QUEUE = 1000;

/** Longest accepted symbol. The longest real subject this relay carries is an
 * option composite key (`SYMBOL_YYYYMMDD_STRIKE_RIGHT`), and a root is at most
 * a handful of characters, so 64 is far above legitimate demand. Without it a
 * single frame could carry a thousand megabyte-long strings for the relay to
 * uppercase, key and hold.
 */
export const MAX_SYMBOL_LENGTH = 64;

export function capItems(raw) {
  return raw.length > MAX_ITEMS_PER_MESSAGE ? raw.slice(0, MAX_ITEMS_PER_MESSAGE) : raw;
}
