/**
 * Per-client input bounds for the realtime relay. A single authenticated
 * client must not be able to make the relay allocate or queue without limit.
 */

/** Largest accepted WebSocket frame (ws defaults to 100 MiB). */
export const MAX_WS_PAYLOAD_BYTES = 1024 * 1024;

/** Most symbols / contracts / indexes honoured from one message. */
export const MAX_ITEMS_PER_MESSAGE = 1000;

/** Most snapshot requests waiting on the IB pacing limiter. */
export const MAX_SNAPSHOT_QUEUE = 1000;

export function capItems(raw) {
  return raw.length > MAX_ITEMS_PER_MESSAGE ? raw.slice(0, MAX_ITEMS_PER_MESSAGE) : raw;
}
