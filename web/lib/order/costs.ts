/**
 * Shared transaction-cost + slippage model (TypeScript side).
 *
 * Mirror of `scripts/costs.py`. Both files MUST model identical numbers so a
 * Python backtest and the live order builder agree on what a fill costs. Edit
 * one, edit the other.
 *
 * Three components, all pure functions (no I/O, no network):
 *
 *   1. `commissionCost`      — IB-style flat per-contract commission with a
 *                              per-order floor.
 *   2. `halfSpreadSlippage`  — half the quoted bid/ask spread, per contract.
 *                              Falls back to an ESTIMATED half-spread when the
 *                              contemporaneous quote is unknown.
 *   3. `comboFillPenalty`    — extra slippage for multi-leg BAG orders, which
 *                              cross more spread than a single instrument.
 *
 * Parameter provenance
 * --------------------
 * The constants below are PLAN PARAMETERS, not empirically fitted values:
 *
 *   - `COMMISSION_PER_CONTRACT` / `COMMISSION_MIN_PER_ORDER` track IBKR's
 *     published US-options fixed schedule (USD 0.65/contract, USD 1.00 order
 *     minimum) as of 2026. A reasonable default, not a contract.
 *   - `ESTIMATED_HALF_SPREAD_PER_CONTRACT` is an ASSUMED fallback for when no
 *     contemporaneous quote is available. It is a modelling assumption, NOT
 *     derived from a fill study. Prefer a real bid/ask whenever one exists.
 *   - `COMBO_LEG_SLIPPAGE_PENALTY` is an assumed per-extra-leg spread cost for
 *     BAG orders. Also a modelling assumption.
 *
 * All slippage / penalty figures are per-share; the `× 100` options multiplier
 * is applied inside each function so callers pass per-share quote values.
 */

/** IBKR US-options per-contract commission (USD). Default, not a contract. */
export const COMMISSION_PER_CONTRACT = 0.65;

/** IBKR per-order minimum commission (USD). Floors tiny single-contract orders. */
export const COMMISSION_MIN_PER_ORDER = 1.0;

/**
 * ASSUMED fallback half-spread per share when no live quote is available.
 * Modelling assumption — NOT empirically derived. Prefer a real bid/ask.
 */
export const ESTIMATED_HALF_SPREAD_PER_CONTRACT = 0.025;

/**
 * ASSUMED extra slippage (per share) for each leg beyond the first in a BAG
 * order. Modelling assumption — NOT empirically derived.
 */
export const COMBO_LEG_SLIPPAGE_PENALTY = 0.01;

/** Standard US options contract multiplier (shares per contract). */
export const OPTION_MULTIPLIER = 100;

/**
 * Flat per-contract commission with a per-order floor.
 *
 * Zero contracts means no order, hence no commission (the floor does NOT
 * apply). Negative counts (a SELL leg) are charged by magnitude.
 */
export function commissionCost(contracts: number): number {
  const count = Math.abs(contracts);
  if (count === 0) return 0;
  return Math.max(COMMISSION_MIN_PER_ORDER, count * COMMISSION_PER_CONTRACT);
}

/**
 * Dollar cost of crossing half the bid/ask spread.
 *
 * When `bid`/`ask` are present and form a valid (non-crossed) quote, the
 * per-share half-spread is `(ask - bid) / 2`. When the quote is missing or
 * crossed/zero-width, fall back to `ESTIMATED_HALF_SPREAD_PER_CONTRACT` so the
 * cost is never understated to $0.
 */
export function halfSpreadSlippage(
  bid: number | null | undefined,
  ask: number | null | undefined,
  contracts: number,
): number {
  return resolveHalfSpread(bid, ask) * Math.abs(contracts) * OPTION_MULTIPLIER;
}

/**
 * Extra slippage for a multi-leg BAG order.
 *
 * A single-leg order pays no penalty. Each leg beyond the first crosses an
 * additional spread, modelled at `COMBO_LEG_SLIPPAGE_PENALTY` per share.
 */
export function comboFillPenalty(numLegs: number, contracts: number): number {
  if (numLegs <= 1) return 0;
  const extraLegs = numLegs - 1;
  return extraLegs * COMBO_LEG_SLIPPAGE_PENALTY * Math.abs(contracts) * OPTION_MULTIPLIER;
}

export interface EntryCostInput {
  contracts: number;
  numLegs: number;
  bid?: number | null;
  ask?: number | null;
}

/** One-way (entry OR exit) cost: commission + half-spread + combo penalty. */
export function totalEntryCost(input: EntryCostInput): number {
  return (
    commissionCost(input.contracts) +
    halfSpreadSlippage(input.bid, input.ask, input.contracts) +
    comboFillPenalty(input.numLegs, input.contracts)
  );
}

export interface RoundTripCostInput {
  contracts: number;
  numLegs: number;
  entryBid?: number | null;
  entryAsk?: number | null;
  exitBid?: number | null;
  exitAsk?: number | null;
}

/**
 * Round-trip cost: entry leg + exit leg.
 *
 * The exit leg uses the exit quote when supplied; otherwise it falls back to
 * the estimated half-spread (the exit quote is usually unknown at entry).
 */
export function estimateRoundTripCost(input: RoundTripCostInput): number {
  const entry = totalEntryCost({
    contracts: input.contracts,
    numLegs: input.numLegs,
    bid: input.entryBid,
    ask: input.entryAsk,
  });
  const exit = totalEntryCost({
    contracts: input.contracts,
    numLegs: input.numLegs,
    bid: input.exitBid,
    ask: input.exitAsk,
  });
  return entry + exit;
}

function resolveHalfSpread(
  bid: number | null | undefined,
  ask: number | null | undefined,
): number {
  if (bid == null || ask == null) return ESTIMATED_HALF_SPREAD_PER_CONTRACT;
  const spread = ask - bid;
  if (spread <= 0) return ESTIMATED_HALF_SPREAD_PER_CONTRACT;
  return spread / 2;
}
