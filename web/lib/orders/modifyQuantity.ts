import type { OpenOrder } from "@/lib/types";

/**
 * Quantity arithmetic for modifying a PARTIALLY FILLED order.
 *
 * Two different numbers meet here and they must not be confused:
 *
 *   - What the operator edits is the REMAINING quantity — the part still
 *     working. A 1000-lot that has filled 16 has 984 left to buy, and 984 is
 *     what the ticket prices, margins and previews.
 *   - What IB accepts on a modify is the NEW TOTAL quantity
 *     (`ib_order_manage.py` assigns `trade.order.totalQuantity`), which
 *     counts the 16 already filled.
 *
 * Sending the remaining figure straight through would silently shrink the
 * order by everything already filled.
 */

/** Filled units, clamped to a value that can be reasoned about.
 *
 * A payload whose `filled` is absent, negative, non-finite, or not smaller
 * than the total is treated as "no partial fill" so the dialog behaves
 * exactly as it always has rather than inventing a remainder.
 */
export function filledQuantity(order: Pick<OpenOrder, "filled" | "totalQuantity">): number {
  const filled = order.filled;
  if (!Number.isFinite(filled) || filled <= 0) return 0;
  if (!Number.isFinite(order.totalQuantity) || filled >= order.totalQuantity) return 0;
  return filled;
}

/** Units still working — what the operator edits and what the ticket prices. */
export function remainingQuantity(
  order: Pick<OpenOrder, "filled" | "totalQuantity">,
): number {
  return order.totalQuantity - filledQuantity(order);
}

/** True when this order has filled in part and is still working. */
export function isPartiallyFilled(
  order: Pick<OpenOrder, "filled" | "totalQuantity">,
): boolean {
  return filledQuantity(order) > 0;
}

/** Translate an edited REMAINING quantity into the NEW TOTAL that IB expects. */
export function toIbTotalQuantity(
  order: Pick<OpenOrder, "filled" | "totalQuantity">,
  enteredRemaining: number,
): number {
  return filledQuantity(order) + enteredRemaining;
}
