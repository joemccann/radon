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
  /** True when this action closes/reduces the target (vs. opening more). */
  isClosing: boolean;
}

const OPT_MULTIPLIER = 100;

function rightOf(leg: PortfolioLeg): "C" | "P" {
  return leg.type === "Call" ? "C" : "P";
}

function cleanExpiry(expiry: string): string {
  return expiry.replace(/-/g, "");
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
}): PositionTradeOrder | null {
  const { position, target, action, quantity, limitPrice, tif, quote } = params;
  const ticker = position.ticker;

  if (target.kind === "combo") {
    if (position.legs.length < 2) return null;
    const legs = position.legs
      .filter((l) => l.strike != null && l.type !== "Stock")
      .map((l) => ({
        expiry: cleanExpiry(position.expiry),
        strike: l.strike as number,
        right: rightOf(l),
        // ComboLeg.action = STRUCTURE (never derived from debit/credit).
        action: (l.direction === "LONG" ? "BUY" : "SELL") as TradeAction,
        ratio: 1,
      }));
    if (legs.length < 2) return null;

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

    // SELL = close/flatten the held combo → close-out branch (proceeds + P&L).
    if (action === "SELL") {
      return {
        isClosing: true,
        payload,
        riskInput: {
          ticker,
          chainLegs: [],
          netPremium: limitPrice,
          description,
          totalCost,
          closeOut: { entryCostDollars: resolveEntryCost(position) },
        },
      };
    }

    // BUY = add to / re-open the combo → hand legs to the augmenter.
    return {
      isClosing: false,
      payload,
      riskInput: {
        ticker,
        chainLegs: legs.map((l) => ({
          action: l.action,
          right: l.right,
          strike: l.strike,
          expiry: l.expiry,
          quantity,
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
  const expiryClean = cleanExpiry(position.expiry);
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

  const closingLong = leg.direction === "LONG" && action === "SELL";
  const closingShort = leg.direction === "SHORT" && action === "BUY";
  // Per-contract basis magnitude (avg_cost is already ×100 for options).
  const basisMagnitude = quantity * Math.abs(leg.avg_cost);

  if (closingLong) {
    // Sell-to-close a long leg: receive proceeds; basis is what we paid.
    return {
      isClosing: true,
      payload,
      riskInput: {
        ticker,
        chainLegs: [],
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
        chainLegs: [],
        netPremium: limitPrice,
        description,
        totalCost: -grossCash, // negative: debit paid
        totalLabel: "Close Debit:",
        closeOut: { entryCostDollars: -basisMagnitude },
      },
    };
  }

  // Opening / adding to a leg (BUY a long, SELL more short): hand the single
  // chain leg to the augmenter so portfolio coverage attaches automatically.
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
          expiry: position.expiry,
          quantity,
        },
      ],
      netPremium: action === "SELL" ? -limitPrice : limitPrice,
      description,
      totalCost: action === "SELL" ? -grossCash : grossCash,
      quote: quote ?? null,
    },
  };
}
