/**
 * Paper-mode routing (Feature F13).
 *
 * Single source of truth for "where does this order go" once the operator has
 * picked Paper vs Live on the OrderRiskGate. Paper mode routes to the
 * homegrown shadow-fill engine (`scripts/paper/`); live mode routes to IB.
 *
 * LIMITATION — the shadow engine cannot reproduce IB-side combo-router drops
 * (e.g. bearish risk reversals that hang in PendingSubmit forever). For
 * faithful combo-router behaviour, point the order path at the IBKR NATIVE
 * paper account on port 7497 instead of the shadow engine. See
 * `feedback_ib_combo_router_silent_drops_bearish_rr.md`.
 */

export type PlacementTarget = "ib" | "paper-shadow";

/** Resolve the placement target from the Paper-mode flag. */
export function resolvePlacementTarget(paperMode: boolean): PlacementTarget {
  return paperMode ? "paper-shadow" : "ib";
}
