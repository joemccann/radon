/**
 * Pure reducer for IB market-depth position-SHIFT deltas (T-041).
 *
 * IB depth updates address LEVELS BY POSITION: insert shifts every level
 * at/below the position down one, delete shifts them up one, update edits
 * in place (see feedback_ib_market_depth_position_shift). The protocol is
 * contiguous — a valid operation never references a position beyond the
 * current ladder length (insert may append at exactly `length`).
 *
 * An out-of-bounds operation therefore means the ladder has DESYNCED from
 * IB's (a missed or reordered delta). The old inline relay logic papered
 * over that: OOB insert was silently clamped to the tail, OOB update
 * DEGRADED TO AN INSERT (shifting every level below and corrupting all
 * later position-addressed ops), and the ladder was never truncated to the
 * requested row count. This module rejects OOB ops (`applied: false`) so
 * the caller can count desyncs and resubscribe, and enforces `maxRows`.
 */

/**
 * Apply one depth delta to a ladder IN PLACE.
 *
 * @param {Array<{price:number,size:number,marketMaker:string|null}>} ladder
 * @param {{operation:number, position:number, price:number, size:number, marketMaker?:string|null}} delta
 *   operation: 0 = insert, 1 = update, 2 = delete (IB semantics)
 * @param {number} maxRows requested reqMktDepth row budget for this book
 * @returns {{applied: boolean, reason?: string}}
 */
export function applyDepthOp(ladder, delta, maxRows) {
  const { operation, position, price, size } = delta;
  if (!Number.isInteger(position) || position < 0) {
    return { applied: false, reason: "invalid-position" };
  }
  const level = { price, size, marketMaker: delta.marketMaker ?? null };
  switch (operation) {
    case 0: // insert — valid at 0..length (append allowed), never beyond.
      if (position > ladder.length) {
        return { applied: false, reason: "oob-insert" };
      }
      ladder.splice(position, 0, level);
      if (ladder.length > maxRows) {
        ladder.length = maxRows;
      }
      return { applied: true };
    case 1: // update — strictly in place; an OOB update is a desync, not an insert.
      if (position >= ladder.length) {
        return { applied: false, reason: "oob-update" };
      }
      ladder[position] = level;
      return { applied: true };
    case 2: // delete — OOB references a level we do not have.
      if (position >= ladder.length) {
        return { applied: false, reason: "oob-delete" };
      }
      ladder.splice(position, 1);
      return { applied: true };
    default:
      return { applied: false, reason: "unknown-operation" };
  }
}
