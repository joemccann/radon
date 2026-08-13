/**
 * Pure send-admission policy for the WS relay's per-client socket buffer.
 *
 * The relay's sendMessage used to gate on `readyState === OPEN` only. A client
 * that stops draining (suspended laptop, wedged tab, throttled mobile radio)
 * keeps the socket OPEN while its send queue grows, so `ws` buffers every frame
 * on the relay's heap — 10 flushes/second of price + depth + tape batches per
 * client, forever. Nothing bounded that: the ping reaper only fires after 65s
 * of missed pongs, and a stalled-but-alive socket keeps ponging.
 *
 * Two marks, because the two failure modes want different answers:
 *
 *   - soft high-water → DROP coalescable frames. `batch` / `depth-batch` /
 *     `tape-batch` are last-write-wins SNAPSHOTS rebuilt from relay state on the
 *     next 100ms flush, so a dropped one is not lost data — the following frame
 *     carries the same or newer values. Everything else (ping/pong, status,
 *     error, subscribe confirmations, the initial `price` snapshot, snapshots,
 *     search results, depth-unavailable) is an EVENT that cannot be
 *     reconstructed, so it is never dropped below the hard cap.
 *   - hard cap → CLOSE. At this depth the peer is not reading at all (TCP
 *     zero-window or a dead socket); no admission policy reclaims that memory,
 *     only tearing the socket down does. The client's own reconnect/backoff
 *     re-subscribes from scratch.
 */

/**
 * 1 MiB ≈ 70 typical price batches (a 50-symbol batch serializes to ~15 KB).
 * A client that far behind is already showing quotes ≥7s stale, so shedding
 * coalescable snapshots costs it nothing it will not immediately get back.
 */
export const SOFT_HIGH_WATER_BYTES = 1_048_576;

/**
 * 8 MiB = 8× the soft mark. Reaching it means shedding every coalescable frame
 * still did not drain the socket, i.e. the peer is gone rather than slow.
 * Bounds a single stuck client's contribution to the relay heap at ~8 MiB.
 */
export const HARD_CAP_BYTES = 8_388_608;

/**
 * Message kinds that are safe to shed under pressure: each is a full
 * last-write-wins snapshot regenerated on the next flush tick.
 */
export const COALESCABLE_KINDS = new Set(["batch", "depth-batch", "tape-batch"]);

/**
 * @param {string|undefined} kind message `type` field
 * @returns {boolean} true iff dropping this frame loses no unrecoverable state
 */
export function isCoalescableKind(kind) {
  return COALESCABLE_KINDS.has(kind);
}

/**
 * Decide what to do with one outbound frame given the socket's current backlog.
 * Pure: no clock, no socket, no side effects.
 *
 * @param {number} bufferedAmountBytes ws `bufferedAmount` for this client
 * @param {string|undefined} kind message `type` field
 * @returns {"send" | "drop" | "close"}
 */
export function decideSend(bufferedAmountBytes, kind) {
  const buffered = Number(bufferedAmountBytes);
  // A missing/NaN/negative reading must never shed traffic — an unknown backlog
  // is treated as an empty one (the pre-backpressure behavior).
  if (!Number.isFinite(buffered) || buffered <= 0) return "send";
  if (buffered >= HARD_CAP_BYTES) return "close";
  if (buffered >= SOFT_HIGH_WATER_BYTES && isCoalescableKind(kind)) return "drop";
  return "send";
}

/**
 * Clear only an incremental snapshot that was actually admitted, and only
 * entries that were not replaced by a newer tick while delivery ran.
 *
 * @param {Map<string, unknown>} dirty latest unsent values
 * @param {Record<string, unknown>} snapshot values offered to the socket
 * @param {"send" | "drop" | "close" | undefined} decision admission result
 */
export function settleIncrementalBatch(dirty, snapshot, decision) {
  if (decision !== "send") return;
  for (const [key, sentValue] of Object.entries(snapshot)) {
    if (dirty.get(key) === sentValue) dirty.delete(key);
  }
}
