/**
 * Position-deck trade construction.
 *
 * Turns a held position (whole combo OR one selected leg) + a user's
 * action/qty/price into the two things every order surface needs: a branded
 * `OrderRiskInput` for `<OrderRiskGate>` and the `/api/orders/place` payload.
 *
 * This is the ONLY new money-math for the position-deck trade feature; it
 * mirrors the close-out + open conventions already proven in OrderTab's
 * NewOrderForm (single leg) and ComboOrderForm (combo), and adds the missing
 * case: trading ONE leg of a multi-leg combo as its own option order.
 *
 * Conventions preserved (see CLAUDE.md):
 *  - leg.avg_cost is per-CONTRACT for options (already ×100), per-share stocks.
 *  - Combo leg actions encode STRUCTURE (LONG→BUY, SHORT→SELL); the envelope
 *    Order.action encodes DIRECTION (BUY open / SELL close).
 *  - closeOut.entryCostDollars is signed so `pnl = proceeds - entryCostDollars`
 *    is correct for both closing a LONG (proceeds +, basis +) and closing a
 *    SHORT (proceeds −, basis − = the original credit).
 */

import type { PortfolioPosition, PortfolioLeg } from "@/lib/types";
import type { OptionOrderRiskInput } from "@/lib/order";
import { resolveEntryCost } from "@/lib/positionUtils";
import { fmtSignedPrice } from "@/lib/format";

export type TradeTarget = { kind: "combo" } | { kind: "leg"; index: number };
export type TradeAction = "BUY" | "SELL";

export interface PositionTradeOrder {
  riskInput: OptionOrderRiskInput;
  /** Body for POST /api/orders/place. */
  payload: Record<string, unknown>;
  /**
   * True when this action is a PURE close of the target — the traded quantity
   * never exceeds the held size, so the order adds no new exposure and
   * `riskInput.closeOut` carries the realised-P&L basis. An order LARGER than
   * the held size reports false: it opens net-new exposure and routes through
   * the risk-augmentation pipeline instead of the close-out short-circuit.
   */
  isClosing: boolean;
}

const OPT_MULTIPLIER = 100;

function rightOf(leg: PortfolioLeg): "C" | "P" {
  return leg.type === "Call" ? "C" : "P";
}

function cleanExpiry(expiry: string): string {
  return expiry.replace(/-/g, "");
}

/** Contracts of this leg currently held, floored at 0 for malformed rows. */
function heldContracts(leg: PortfolioLeg): number {
  const held = Math.trunc(leg.contracts);
  return Number.isFinite(held) && held > 0 ? held : 0;
}

/** Option legs of the position, in the order the BAG payload emits them. */
function tradeableOptionLegs(position: PortfolioPosition): PortfolioLeg[] {
  return position.legs.filter((l) => l.strike != null && l.type !== "Stock");
}

function greatestCommonDivisor(a: number, b: number): number {
  let x = Math.abs(Math.trunc(a));
  let y = Math.abs(Math.trunc(b));
  while (y !== 0) [x, y] = [y, x % y];
  return x || 1;
}

/**
 * Combo units held: the GCD of the per-leg contract counts — the same
 * reduction `normalizeComboOrder` performs on a chain-built BAG.
 *
 * One unit of the BAG is `ratio_i = contracts_i / units` of each leg, so
 * `units × ratio_i === contracts_i` and a FULL close is exactly `units`
 * combos. `position.contracts` is NOT this number: `ib_sync.py` sets it to the
 * first LONG leg's contract count, which for a 5x3 ratio structure reads 5
 * while the BAG only holds 1 unit of a 5:3 combo.
 */
export function heldComboUnits(position: PortfolioPosition): number {
  const contracts = tradeableOptionLegs(position).map(heldContracts).filter((c) => c > 0);
  if (contracts.length === 0) return 1;
  return contracts.reduce(greatestCommonDivisor);
}

/** True only when a combo SELL stays within the held BAG units. */
export function isPureComboClose(
  action: TradeAction,
  quantity: number,
  heldUnits: number,
): boolean {
  return action === "SELL" && quantity > 0 && quantity <= heldUnits;
}

/** Defense for close-only tickets: a SELL must never exceed held BAG units. */
export function overClosesHeldCombo(
  action: TradeAction,
  quantity: number,
  heldUnits: number,
): boolean {
  return action === "SELL" && quantity > heldUnits;
}

/** The action that CLOSES the target (so the UI can default to it). */
export function closingActionFor(position: PortfolioPosition, target: TradeTarget): TradeAction {
  if (target.kind === "combo") return "SELL"; // SELL envelope flattens the combo
  const leg = position.legs[target.index];
  return leg.direction === "LONG" ? "SELL" : "BUY";
}

/**
 * Build the risk input + placement payload for a position-deck trade.
 * Returns null when the target/leg is not a tradeable option.
 */
export function buildPositionTradeOrder(params: {
  position: PortfolioPosition;
  target: TradeTarget;
  action: TradeAction;
  quantity: number;
  limitPrice: number;
  tif: "DAY" | "GTC";
  /**
   * FU7: optional live entry quote (net combo or single-leg, per-unit positive
   * magnitudes). Threaded into the OPENING branches only so the F1 cost model
   * renders net-of-cost max-loss/max-gain. Close-out branches surface realised
   * P&L and have no risk math to adjust, so the quote is ignored there.
   */
  quote?: { bid: number | null; ask: number | null } | null;
  /**
   * Phase-1 margin: already-resolved underlying spot for the order's symbol.
   * Threaded into the single-leg OPENING branch so a naked short surfaces a
   * Reg-T margin requirement (not the assignment-at-zero `maxLoss`). Null when
   * the caller has no spot.
   */
  underlyingSpot?: number | null;
}): PositionTradeOrder | null {
  const { position, target, action, quantity, limitPrice, tif, quote, underlyingSpot } = params;
  const ticker = position.ticker;

  if (target.kind === "combo") {
    if (position.legs.length < 2) return null;
    const optionLegs = tradeableOptionLegs(position);
    if (optionLegs.length < 2) return null;
    // ComboLeg.ratio encodes the HELD structure. A 5x3 ratio reverse risk
    // reversal is a 5:3 BAG — shipping 1:1 makes IB trade equal contracts per
    // side, over-trading the smaller leg. Ratios are the contract counts
    // reduced by their GCD, so `payload.quantity` counts combo UNITS and
    // `quantity × ratio_i` is the contracts traded on leg i.
    const comboUnits = heldComboUnits(position);
    const legs = optionLegs.map((l) => ({
      expiry: cleanExpiry(l.expiry ?? position.expiry),
      strike: l.strike as number,
      right: rightOf(l),
      // ComboLeg.action = STRUCTURE (never derived from debit/credit).
      action: (l.direction === "LONG" ? "BUY" : "SELL") as TradeAction,
      // Floor at 1: IB rejects a zero-ratio BAG leg, so a malformed
      // zero-contract row still ships a routable combo.
      ratio: Math.max(1, heldContracts(l) / comboUnits),
    }));

    const payload = {
      type: "combo",
      symbol: ticker,
      action,
      quantity,
      limitPrice,
      tif,
      legs,
    };

    const totalCost = quantity * limitPrice * OPT_MULTIPLIER;
    const description = `${action} ${quantity}x ${position.structure} @ ${fmtSignedPrice(limitPrice)}`;

    // SELL closes only up to the held BAG units. Partial closes carry the
    // proportional signed basis; an over-close must enter normal risk math.
    if (isPureComboClose(action, quantity, comboUnits)) {
      return {
        isClosing: true,
        payload,
        riskInput: {
          ticker,
          chainLegs: legs.map((leg) => ({
            action: leg.action === "BUY" ? "SELL" : "BUY",
            right: leg.right,
            strike: leg.strike,
            expiry: leg.expiry,
            quantity: quantity * leg.ratio,
          })),
          netPremium: limitPrice,
          description,
          totalCost,
          closeOut: {
            entryCostDollars: resolveEntryCost(position) * (quantity / comboUnits),
          },
        },
      };
    }

    if (action === "SELL") {
      return {
        isClosing: false,
        payload,
        riskInput: {
          ticker,
          chainLegs: legs.map((leg) => ({
            action: leg.action === "BUY" ? "SELL" : "BUY",
            right: leg.right,
            strike: leg.strike,
            expiry: leg.expiry,
            quantity: quantity * leg.ratio,
          })),
          netPremium: -limitPrice,
          description,
          totalCost,
          quote: quote ?? null,
        },
      };
    }

    // BUY = add to / re-open the combo → hand legs to the augmenter.
    return {
      isClosing: false,
      payload,
      riskInput: {
        ticker,
        // `ChainOrderLeg.quantity` is contractually the RAW contract count for
        // the leg (the augmenter derives the per-combo ratio itself). For a
        // ratio BAG that is `quantity × ratio`, not `quantity` — passing the
        // combo count for every leg makes the risk model price a 1:1 structure.
        chainLegs: legs.map((l) => ({
          action: l.action,
          right: l.right,
          strike: l.strike,
          expiry: l.expiry,
          quantity: quantity * l.ratio,
        })),
        netPremium: limitPrice,
        description,
        totalCost,
        quote: quote ?? null,
      },
    };
  }

  // ── single leg of the position ───────────────────────────────────────────
  const leg = position.legs[target.index];
  if (!leg || leg.strike == null || leg.type === "Stock") return null;

  const right = rightOf(leg);
  const strike = leg.strike;
  const expiryClean = cleanExpiry(leg.expiry ?? position.expiry);
  const grossCash = quantity * limitPrice * OPT_MULTIPLIER;
  const legLabel = `${leg.type} $${strike}`;
  const description = `${action} ${quantity}x ${ticker} ${legLabel} @ ${fmtSignedPrice(limitPrice)}`;

  const payload = {
    type: "option",
    symbol: ticker,
    action,
    quantity,
    limitPrice,
    tif,
    expiry: expiryClean,
    strike,
    right,
  };

  // A trade is a close only up to the HELD size. SELL 10 against 5 held longs
  // closes 5 and OPENS 5 naked shorts; routing that through `closeOut` makes
  // `useOrderRisk` short-circuit max-loss/max-gain to 0 and hide the residue.
  // Mirrors `computeLinearRisk`'s partial-cover split: at-or-below held size is
  // a pure close, above it the whole order goes to the augmentation pipeline so
  // held coverage nets the closed portion and only the excess reads as naked.
  const held = heldContracts(leg);
  const withinHeldSize = quantity <= held;
  const closingLong = leg.direction === "LONG" && action === "SELL" && withinHeldSize;
  const closingShort = leg.direction === "SHORT" && action === "BUY" && withinHeldSize;
  // Per-contract basis magnitude (avg_cost is already ×100 for options). Only
  // the closed portion carries basis — never more contracts than are held.
  const basisMagnitude = Math.min(quantity, held) * Math.abs(leg.avg_cost);

  if (closingLong) {
    // Sell-to-close a long leg: receive proceeds; basis is what we paid.
    return {
      isClosing: true,
      payload,
      riskInput: {
        ticker,
        chainLegs: [{ action: "SELL", right, strike, expiry: expiryClean, quantity }],
        netPremium: -limitPrice,
        description,
        totalCost: grossCash, // positive: proceeds received
        totalLabel: "Proceeds:",
        closeOut: { entryCostDollars: basisMagnitude },
      },
    };
  }

  if (closingShort) {
    // Buy-to-close a short leg: pay debit; original basis was a CREDIT, so it
    // is negative. pnl = proceeds - entryCost = (-debit) - (-credit) = credit - debit.
    return {
      isClosing: true,
      payload,
      riskInput: {
        ticker,
        chainLegs: [{ action: "BUY", right, strike, expiry: expiryClean, quantity }],
        netPremium: limitPrice,
        description,
        totalCost: -grossCash, // negative: debit paid
        totalLabel: "Close Debit:",
        closeOut: { entryCostDollars: -basisMagnitude },
      },
    };
  }

  // Opening / adding to a leg (BUY a long, SELL more short) OR over-closing it
  // (quantity > held): hand the single chain leg — at its RAW order quantity,
  // never pre-netted — to the augmenter so held coverage attaches automatically
  // and only the uncovered excess is modelled as naked.
  return {
    isClosing: false,
    payload,
    riskInput: {
      ticker,
      chainLegs: [
        {
          action,
          right,
          strike,
          expiry: leg.expiry ?? position.expiry,
          quantity,
        },
      ],
      netPremium: action === "SELL" ? -limitPrice : limitPrice,
      description,
      totalCost: action === "SELL" ? -grossCash : grossCash,
      quote: quote ?? null,
      underlyingSpot: underlyingSpot ?? null,
    },
  };
}
