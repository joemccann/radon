/**
 * Paper-mode routing (Feature F13).
 *
 * Single source of truth for "where does this order go" once the operator has
 * picked Paper vs Live on the OrderRiskGate. Paper mode routes to the
 * homegrown shadow-fill engine (`scripts/paper/`); live mode routes to IB.
 *
 * The shadow engine fills marketable orders locally. A paper fill is not an
 * IB acknowledgement. An unacknowledged live order has an unknown outcome;
 * reconcile open orders before any new submit. See the order placement
 * contract in `scripts/CLAUDE.md`. TWS paper is port 7497.
 */

export type PlacementTarget = "ib" | "paper-shadow";

/** Resolve the placement target from the Paper-mode flag. */
export function resolvePlacementTarget(paperMode: boolean): PlacementTarget {
  return paperMode ? "paper-shadow" : "ib";
}
