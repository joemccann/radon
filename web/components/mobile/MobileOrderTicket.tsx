"use client";

import { useEffect, useMemo, useState } from "react";
import { Minus, Plus, X } from "lucide-react";
import {
  type OrderLeg,
  computeNetPrice,
  computeNetOptionQuote,
  detectStructure,
  getComboEntryAction,
  normalizeComboOrder,
  formatExpiry,
} from "@/lib/optionsChainUtils";
import { normalizeOptionExpiry } from "@/lib/pricesProtocol";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData } from "@/lib/types";
import { fmtPrice } from "@/lib/positionUtils";
import {
  OrderRiskGate,
  useOrderRisk,
  type OrderRiskInput,
  type OrderRiskState,
} from "@/lib/order";
import BuySellRow from "./BuySellRow";
import BottomSheet from "./BottomSheet";
import ComboSkewPanel from "@/components/ComboSkewPanel";
import OrderErrorBanner from "@/components/OrderErrorBanner";
import {
  type IbOrderType,
  IB_ORDER_TYPES,
  ibPlaceFields,
  isStopOrderType,
  orderTypeLabel,
  pricesValidForOrderType,
} from "@/lib/order/stopOrder";

type MobileOrderTicketProps = {
  open: boolean;
  ticker: string;
  legs: OrderLeg[];
  prices: Record<string, PriceData>;
  spot?: number | null;
  riskFreeRate?: number;
  /**
   * Live portfolio snapshot — routed into `<OrderRiskGate>` for held-LONG
   * coverage detection. `null` is acceptable (gate renders "Coverage
   * indeterminate" + disables submit); `undefined` triggers the pending
   * skeleton.
   */
  portfolio?: PortfolioData | null;
  onClose: () => void;
  onRemoveLeg: (id: string) => void;
  onUpdateLeg: (id: string, updates: Partial<OrderLeg>) => void;
  onClearLegs: () => void;
};

/** P2: adaptive limit-price tick. Sub-$3 options quote in pennies; $3+ quote
 *  in nickels. Derived from the current limit/mid magnitude so the ± steppers
 *  feel right per underlying. Behavior unchanged at or above $3. */
function priceTick(magnitude: number): number {
  return Math.abs(magnitude) < 3 ? 0.01 : 0.05;
}

/** P6: additive quick-qty presets beyond the ±1 steppers — each tap ADDS its
 *  value to the leg quantity (10 then +25 → 35). */
const QTY_PRESETS = [5, 10, 25, 50, 100] as const;

/** Held-contract match for the single-leg close-out branch. `avgCost` is the
 *  per-contract basis (already × 100 for options — NEVER re-multiply). */
type HeldContract = { direction: "LONG" | "SHORT"; contracts: number; avgCost: number };

/**
 * Search the portfolio for the exact option contract this leg trades
 * (ticker + expiry + strike + right) and return its held direction, size,
 * and per-contract basis. Single-leg only — multi-leg combos stay OPEN
 * (documented Phase-1 scope limit; combo close detection is deferred).
 */
function findHeldContract(
  portfolio: PortfolioData | null | undefined,
  ticker: string,
  leg: OrderLeg,
): HeldContract | null {
  if (!portfolio?.positions) return null;
  const legExpiry = normalizeOptionExpiry(leg.expiry);
  if (legExpiry == null) return null;
  const legType = leg.right === "C" ? "Call" : "Put";
  for (const pos of portfolio.positions) {
    if ((pos.ticker ?? "").toUpperCase() !== ticker.toUpperCase()) continue;
    if (normalizeOptionExpiry(pos.expiry ?? "") !== legExpiry) continue;
    for (const held of pos.legs) {
      if (held.type !== legType) continue;
      if (held.strike == null || held.strike !== leg.strike) continue;
      return {
        direction: held.direction,
        contracts: Math.abs(held.contracts),
        avgCost: held.avg_cost,
      };
    }
  }
  return null;
}

/** P6: contracts of this leg's exact option held in the closing direction
 *  (a SELL closes a held LONG; a BUY closes a held SHORT). 0 = the leg opens
 *  rather than closes, so no "Max" affordance is offered. Reuses the Phase-1
 *  `findHeldContract` lookup. */
function closableContracts(
  portfolio: PortfolioData | null | undefined,
  ticker: string,
  leg: OrderLeg,
): number {
  const held = findHeldContract(portfolio, ticker, leg);
  if (!held) return 0;
  if (leg.action === "SELL" && held.direction === "LONG") return held.contracts;
  if (leg.action === "BUY" && held.direction === "SHORT") return held.contracts;
  return 0;
}

function fmtDollars(value: number): string {
  return Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function legDescription(leg: OrderLeg): string {
  return `${leg.quantity}x $${leg.strike} ${leg.right === "C" ? "Call" : "Put"} ${formatExpiry(leg.expiry)}`;
}

function legMidPrice(leg: OrderLeg, prices: Record<string, PriceData>): string {
  // Build the option key to look up live bid/ask
  const key = `${leg.expiry}_${leg.strike}_${leg.right}`;
  // Try to find a matching price key in the prices map
  const matchKey = Object.keys(prices).find((k) => k.includes(key));
  if (matchKey) {
    const pd = prices[matchKey];
    if (pd?.bid != null && pd?.ask != null) {
      return fmtPrice((pd.bid + pd.ask) / 2);
    }
    if (pd?.last != null) return fmtPrice(pd.last);
  }
  return leg.limitPrice != null ? fmtPrice(leg.limitPrice) : "---";
}

export default function MobileOrderTicket({
  open,
  ticker,
  legs,
  prices,
  spot = null,
  riskFreeRate,
  portfolio = null,
  onClose,
  onRemoveLeg,
  onUpdateLeg,
  onClearLegs,
}: MobileOrderTicketProps) {
  const [tif, setTif] = useState<"DAY" | "GTC">("DAY");
  const [orderType, setOrderType] = useState<IbOrderType>("LMT");
  const [stopPriceText, setStopPriceText] = useState<string>("");
  const [limitPriceText, setLimitPriceText] = useState<string>("");
  const [priceManuallySet, setPriceManuallySet] = useState(false);
  const [confirmStep, setConfirmStep] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [riskState, setRiskState] = useState<OrderRiskState | null>(null);

  const isCombo = legs.length > 1;
  const normalizedOrder = useMemo(() => (isCombo ? normalizeComboOrder(legs) : null), [isCombo, legs]);
  const pricingLegs = normalizedOrder?.legs ?? legs;
  const structure = detectStructure(legs);
  const netPrice = computeNetPrice(pricingLegs, prices);
  const isDebit = netPrice != null ? netPrice > 0 : null;
  const totalQty = normalizedOrder?.quantity ?? (legs.length > 0 ? legs[0].quantity : 1);

  const quotingLegs = useMemo(() => {
    if (legs.length === 0) return legs;
    return normalizeComboOrder(legs).legs;
  }, [legs]);
  const netQuote = useMemo(() => computeNetOptionQuote(quotingLegs, prices, ticker), [quotingLegs, prices, ticker]);

  const signedNet = (value: number | null): number | null => {
    if (value == null) return null;
    if (!isCombo) return Math.abs(value);
    if (isDebit === null) return value;
    return isDebit ? Math.abs(value) : -Math.abs(value);
  };

  const signedQuote = useMemo(
    () => ({
      bid: signedNet(netQuote.bid),
      mid: signedNet(netQuote.mid),
      ask: signedNet(netQuote.ask),
    }),
    [netQuote.bid, netQuote.mid, netQuote.ask, isCombo, isDebit],
  );

  // Auto-populate to mid on first availability + on structure changes.
  useEffect(() => {
    if (priceManuallySet) return;
    if (signedQuote.mid == null) return;
    setLimitPriceText(signedQuote.mid.toFixed(2));
  }, [signedQuote.mid, priceManuallySet]);

  // Reset manual-set AND the confirm step when the leg structure changes
  // (action/right/strike/qty differ). Any structural edit must drop the
  // operator back to the build view so they re-review — mirrors desktop.
  const structureKey = useMemo(
    () => legs.map((l) => `${l.action}-${l.right}-${l.strike}-${l.expiry}-${l.quantity}`).join("|"),
    [legs],
  );
  const lastStructureRef = useMemo(() => ({ current: "" }), []);
  useEffect(() => {
    if (structureKey === lastStructureRef.current) return;
    lastStructureRef.current = structureKey;
    setPriceManuallySet(false);
    setConfirmStep(false);
  }, [structureKey, lastStructureRef]);

  // Any edit to price or TIF must also invalidate the confirm step.
  const resetConfirm = () => setConfirmStep(false);

  const parsedPrice = parseFloat(limitPriceText);
  const parsedStop = parseFloat(stopPriceText);
  const isValidPrice = isCombo
    ? !isNaN(parsedPrice) && parsedPrice !== 0
    : pricesValidForOrderType({ orderType, limitPrice: parsedPrice, stopPrice: parsedStop });
  const isValid = isValidPrice && legs.length > 0;
  const signedLimitPrice = Number.isFinite(parsedPrice)
    ? isDebit === null
      ? parsedPrice
      : isDebit
        ? Math.abs(parsedPrice)
        : -Math.abs(parsedPrice)
    : NaN;

  // Single-leg close-out detection. Multi-leg combos stay OPEN (Phase-1 scope
  // limit). A SELL against a held LONG (>= qty) is a SELL-to-close; a BUY
  // against a held SHORT (>= qty) is a BUY-to-close.
  const singleLeg = !isCombo && legs.length === 1 ? legs[0] : null;
  const heldContract = useMemo(
    () => (singleLeg ? findHeldContract(portfolio, ticker, singleLeg) : null),
    [singleLeg, portfolio, ticker],
  );
  const closeKind = useMemo<"sell-to-close" | "buy-to-close" | null>(() => {
    if (!singleLeg || !heldContract) return null;
    const qty = Math.max(1, Math.trunc(singleLeg.quantity));
    if (singleLeg.action === "SELL" && heldContract.direction === "LONG" && heldContract.contracts >= qty) {
      return "sell-to-close";
    }
    if (singleLeg.action === "BUY" && heldContract.direction === "SHORT" && heldContract.contracts >= qty) {
      return "buy-to-close";
    }
    return null;
  }, [singleLeg, heldContract]);

  // Build the chokepoint input — every order surface that displays risk math
  // MUST route through `<OrderRiskGate>`. Mobile was a structural gap before
  // (commit 6a14278 landed desktop; this is the mobile equivalent). Same
  // single-leg credit-sign rule applies: a SELL leg is structurally a credit
  // regardless of whether live WS bid/ask have populated.
  const riskInput: OrderRiskInput | null = useMemo(() => {
    if (!isValidPrice || legs.length === 0) return null;
    const totalCost = parsedPrice * totalQty * 100;
    const description = `${structure || "Option"} @ ${fmtPrice(parsedPrice)}`;

    // Close-out branch (single-leg only). Mirrors OrderTab: the gate's
    // `closeOut` short-circuit surfaces proceeds + realized P&L. `avgCost` is
    // per-contract for options — used directly, NEVER × 100 (the d420c16 100x
    // bug that read −$635,055 on a $19,389.45 winner).
    if (singleLeg && closeKind && heldContract) {
      const qty = Math.max(1, Math.trunc(singleLeg.quantity));
      const gross = parsedPrice * qty * 100;
      if (closeKind === "sell-to-close") {
        return {
          ticker,
          chainLegs: [],
          netPremium: -parsedPrice,
          description,
          totalCost: gross,
          totalLabel: "Proceeds:",
          closeOut: { entryCostDollars: qty * heldContract.avgCost },
        };
      }
      // buy-to-close-short: pnl = proceeds(−debit) − entryCost(−credit) =
      // credit − debit.
      return {
        ticker,
        chainLegs: [],
        netPremium: parsedPrice,
        description,
        totalCost: -gross,
        totalLabel: "Close Debit:",
        closeOut: { entryCostDollars: -(qty * Math.abs(heldContract.avgCost)) },
      };
    }

    const isCredit = isCombo
      ? isDebit === false
      : legs[0]?.action === "SELL";
    const netPremium = isCredit ? -Math.abs(parsedPrice) : parsedPrice;
    const chainLegs = (normalizedOrder?.legs ?? legs).map((l) => ({
      action: l.action,
      right: l.right,
      strike: l.strike,
      expiry: l.expiry,
      // normalizeComboOrder has divided multi-leg by GCD; single-leg passes
      // through with raw user-entered count (the hook re-normalises).
      quantity: normalizedOrder ? l.quantity : Math.max(1, Math.trunc(l.quantity)),
    }));
    return {
      ticker,
      chainLegs,
      netPremium,
      description,
      totalCost: isCredit ? -totalCost : totalCost,
      // FU7: net entry quote for net-of-cost risk. `netQuote` is the unsigned
      // per-unit combo quote; null bid/ask falls back to the F1 estimate.
      quote: { bid: netQuote.bid, ask: netQuote.ask },
      // Phase-1 margin: underlying spot for the naked-short Reg-T estimate.
      underlyingSpot: spot ?? null,
    };
  }, [isValidPrice, parsedPrice, totalQty, structure, isDebit, isCombo, legs, normalizedOrder, ticker, netQuote.bid, netQuote.ask, spot, singleLeg, closeKind, heldContract]);

  // Build-view teaser: a lightweight read of the same risk math the confirm
  // gate renders in full. The gate stays the single chokepoint; this is a
  // read-only preview so the operator sees max loss / max gain before Review.
  const teaserState = useOrderRisk(riskInput, portfolio);

  const okToSubmit = riskState?.okToSubmit !== false; // null (pre-confirm) allowed

  // P2: step by an adaptive tick derived from the current price magnitude.
  const adjustPrice = (direction: 1 | -1) => {
    const base = parseFloat(limitPriceText) || signedQuote.mid || 0;
    const next = base + direction * priceTick(base);
    setLimitPriceText(next.toFixed(2));
    setPriceManuallySet(true);
    resetConfirm();
  };

  // F3: one-tap quote quick-set. Mirrors PositionTradeTicket BID/MID/ASK
  // buttons — writes the abs magnitude into the text input (same as the ±
  // stepper), marks the price manual, and drops back to the build view.
  const setPriceFromQuote = (value: number | null) => {
    if (value == null) return;
    const next = Math.abs(value).toFixed(2);
    if (!isCombo && isStopOrderType(orderType)) {
      setStopPriceText(next);
    } else {
      setLimitPriceText(next);
    }
    setPriceManuallySet(true);
    resetConfirm();
  };

  const handleSubmit = async () => {
    // First tap only advances to confirm (mirror PositionTradeTicket): the
    // Review button sets confirmStep; handleSubmit never places until then.
    if (!confirmStep) return;
    if (!isValid) return;
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const comboOrder = normalizedOrder ?? normalizeComboOrder(legs);
      const body = isCombo
        ? {
            type: "combo",
            symbol: ticker,
            action: getComboEntryAction(comboOrder.legs),
            quantity: totalQty,
            limitPrice: signedLimitPrice,
            tif,
            legs: comboOrder.legs.map((l) => ({
              symbol: ticker,
              secType: "OPT",
              expiry: normalizeOptionExpiry(l.expiry) ?? l.expiry,
              strike: l.strike,
              right: l.right === "C" ? "CALL" : "PUT",
              action: l.action,
              ratio: l.quantity,
              ...(l.limitPrice != null ? { limitPrice: l.limitPrice } : {}),
            })),
          }
        : {
            type: "option",
            symbol: ticker,
            action: legs[0].action,
            quantity: legs[0].quantity,
            tif,
            expiry: normalizeOptionExpiry(legs[0].expiry) ?? legs[0].expiry,
            strike: legs[0].strike,
            right: legs[0].right === "C" ? "CALL" : "PUT",
            ...ibPlaceFields(orderType, parsedPrice, parsedStop),
          };

      const res = await fetch("/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || "Order placement failed");
      } else {
        setSuccess(`Placed ${structure || "Order"} on ${ticker}`);
        setConfirmStep(false);
        // Keep success message visible while the user reads it; clear legs +
        // close the sheet together after a brief delay so the parent doesn't
        // unmount us prematurely (the parent open flag depends on legs.length).
        setTimeout(() => {
          onClearLegs();
          onClose();
        }, 800);
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  // Direction label sits ABOVE the price in the submit button.
  // Net CREDIT -> red (.m-submit--credit); Net DEBIT -> green (.m-submit--debit).
  const netIsCredit = isDebit === false;
  const submitColorClass = isValidPrice
    ? netIsCredit
      ? "m-submit--credit"
      : "m-submit--debit"
    : "";

  const directionLabel =
    closeKind === "sell-to-close"
      ? "SELL TO CLOSE"
      : closeKind === "buy-to-close"
        ? "BUY TO CLOSE"
        : netIsCredit
          ? "SELL TO OPEN"
          : "BUY TO OPEN";

  // Teasers (build view). Notional = |limit| × totalQty × 100. The verb tracks
  // the cash direction: a debit is paid, a credit is received.
  const notionalDollars = isValidPrice ? Math.abs(parsedPrice) * totalQty * 100 : null;
  const notionalVerb = netIsCredit ? "receive" : "pay";
  const riskTeaser = (() => {
    if (!teaserState) return null;
    const s = teaserState.summary;
    if (s.estimatedPnl != null) {
      const label = s.estimatedPnlLabel ?? "Est. realized P&L";
      const sign = s.estimatedPnl >= 0 ? "+" : "-";
      return `${label.replace(/:$/, "")} ${sign}$${fmtDollars(s.estimatedPnl)}`;
    }
    const bound = (val: number | null | undefined, unbounded: boolean | undefined) =>
      unbounded ? "Unbounded" : val == null ? "n/a" : `$${fmtDollars(val)}`;
    return `Max loss ${bound(s.maxLoss, s.maxLossUnbounded)} / Max gain ${bound(s.maxGain, s.maxGainUnbounded)}`;
  })();

  const statusBlock = (
    <>
      <OrderErrorBanner error={error} />
      {success ? (
        <div className="mobile-ticket__success" data-testid="mobile-order-ticket-success">
          {success}
        </div>
      ) : null}
    </>
  );

  const footer = confirmStep ? (
    <>
      {statusBlock}
      <div className="mobile-ticket__footer-row">
        <button
          type="button"
          className="mobile-ticket__back tap-target"
          onClick={() => setConfirmStep(false)}
          disabled={submitting}
          data-testid="mobile-order-ticket-back"
        >
          Back
        </button>
        <button
          type="button"
          className={`mobile-ticket__submit${submitColorClass ? ` ${submitColorClass}` : ""}`}
          onClick={handleSubmit}
          disabled={!isValid || submitting || legs.length === 0 || !okToSubmit}
          data-testid="mobile-order-ticket-submit"
        >
          {submitting ? (
            "Placing..."
          ) : (
            <span className="mobile-ticket__submit-inner">
              <span className="mobile-ticket__submit-direction">{directionLabel}</span>
              <span className="mobile-ticket__submit-net">Confirm &amp; send</span>
            </span>
          )}
        </button>
      </div>
    </>
  ) : (
    <>
      {statusBlock}
      {riskTeaser ? (
        <div className="mobile-ticket__teaser" data-testid="mobile-order-ticket-teaser">
          {notionalDollars != null ? (
            <span className="mobile-ticket__teaser-line">
              You&apos;ll {notionalVerb} ${fmtDollars(notionalDollars)}
            </span>
          ) : null}
          <span className="mobile-ticket__teaser-line mobile-ticket__teaser-risk">{riskTeaser}</span>
        </div>
      ) : null}
      <div className="mobile-ticket__footer-row">
        <button
          type="button"
          className="mobile-ticket__clear tap-target"
          onClick={onClearLegs}
          disabled={legs.length === 0}
          data-testid="mobile-order-ticket-clear"
        >
          Clear
        </button>
        <button
          type="button"
          className={`mobile-ticket__submit${submitColorClass ? ` ${submitColorClass}` : ""}`}
          onClick={() => setConfirmStep(true)}
          disabled={!isValid || legs.length === 0}
          data-testid="mobile-order-ticket-review"
        >
          <span className="mobile-ticket__submit-inner">
            <span className="mobile-ticket__submit-direction">{directionLabel}</span>
            <span className="mobile-ticket__submit-net">Review order</span>
          </span>
        </button>
      </div>
    </>
  );

  return (
    <BottomSheet
      open
      onClose={onClose}
      title={`${ticker.toUpperCase()} · ${structure || "Order"}`}
      testId="mobile-order-ticket"
      maxHeight="82dvh"
      footer={footer}
    >
      {confirmStep ? (
        <div className="mobile-ticket mobile-ticket--confirm">
          {/* Order recap — direction, net, notional, TIF (proposal §C) */}
          <div className="mobile-ticket__confirm-recap" data-testid="mobile-order-ticket-recap">
            <div className="mobile-ticket__confirm-row">
              <span className="mobile-ticket__confirm-label">Direction</span>
              <span className="mobile-ticket__confirm-value">{directionLabel}</span>
            </div>
            <div className="mobile-ticket__confirm-row">
              <span className="mobile-ticket__confirm-label">{netIsCredit ? "Net credit" : "Net debit"}</span>
              <span className="mobile-ticket__confirm-value">
                {Number.isFinite(parsedPrice) ? fmtPrice(Math.abs(parsedPrice)) : "—"}
              </span>
            </div>
            {notionalDollars != null ? (
              <div className="mobile-ticket__confirm-row">
                <span className="mobile-ticket__confirm-label">You&apos;ll {notionalVerb}</span>
                <span className="mobile-ticket__confirm-value">${fmtDollars(notionalDollars)}</span>
              </div>
            ) : null}
            <div className="mobile-ticket__confirm-row">
              <span className="mobile-ticket__confirm-label">Time in force</span>
              <span className="mobile-ticket__confirm-value">{tif}</span>
            </div>
          </div>

          {/* Full risk summary — the single OrderRiskGate chokepoint. */}
          <div className="mobile-ticket__risk" data-testid="mobile-order-ticket-risk">
            <OrderRiskGate
              input={riskInput}
              portfolio={portfolio}
              surface="mobile-ticket"
              variant="info"
              onState={setRiskState}
            />
          </div>
        </div>
      ) : (
        <div className="mobile-ticket">
          {/* 1. Legs — using BuySellRow for visual accent + 44px touch targets */}
          <div className="mobile-ticket__legs" data-testid="mobile-order-ticket-legs">
            {legs.map((leg) => {
              const maxClose = closableContracts(portfolio, ticker, leg);
              return (
              <div key={leg.id} className="mobile-ticket__leg-row">
                <BuySellRow
                  side={leg.action}
                  label={legDescription(leg)}
                  price={legMidPrice(leg, prices)}
                  sub={`${leg.right === "C" ? "Call" : "Put"} · ${formatExpiry(leg.expiry)}`}
                />
                <div className="mobile-ticket__leg-controls">
                  {/* F4: flip BUY<->SELL. A side change is a STRUCTURE edit —
                      the structureKey effect (includes l.action) drops the
                      manual price + confirm step back to build view. */}
                  <button
                    type="button"
                    className={`mobile-ticket__leg-side mobile-ticket__leg-side--${leg.action === "BUY" ? "buy" : "sell"} tap-target`}
                    aria-label={`Flip side, currently ${leg.action === "BUY" ? "Buy" : "Sell"}`}
                    onClick={() => onUpdateLeg(leg.id, { action: leg.action === "BUY" ? "SELL" : "BUY" })}
                    data-testid={`mobile-order-ticket-leg-${leg.id}-flip`}
                  >
                    {leg.action}
                  </button>
                  <button
                    type="button"
                    className="mobile-ticket__qty-btn tap-target"
                    aria-label="Decrease quantity"
                    onClick={() => onUpdateLeg(leg.id, { quantity: Math.max(1, leg.quantity - 1) })}
                    data-testid={`mobile-order-ticket-leg-${leg.id}-minus`}
                  >
                    <Minus size={16} aria-hidden />
                  </button>
                  <span className="mobile-ticket__qty-value">{leg.quantity}</span>
                  <button
                    type="button"
                    className="mobile-ticket__qty-btn tap-target"
                    aria-label="Increase quantity"
                    onClick={() => onUpdateLeg(leg.id, { quantity: leg.quantity + 1 })}
                    data-testid={`mobile-order-ticket-leg-${leg.id}-plus`}
                  >
                    <Plus size={16} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="mobile-ticket__leg-remove tap-target"
                    aria-label="Remove leg"
                    onClick={() => onRemoveLeg(leg.id)}
                    data-testid={`mobile-order-ticket-leg-${leg.id}-remove`}
                  >
                    <X size={16} aria-hidden />
                  </button>
                </div>
                {/* P6: additive quick-qty presets beyond ±1 — each chip ADDS
                    its value to the current quantity. "Max" appears only when
                    this leg closes a held position, SETTING to the held
                    contracts. */}
                <div
                  className="mobile-ticket__qty-presets"
                  data-testid={`mobile-order-ticket-leg-${leg.id}-presets`}
                >
                  {QTY_PRESETS.map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      className="mobile-ticket__qty-preset tap-target"
                      aria-label={`Add ${preset} to quantity`}
                      onClick={() => onUpdateLeg(leg.id, { quantity: leg.quantity + preset })}
                      data-testid={`mobile-order-ticket-leg-${leg.id}-qty-${preset}`}
                    >
                      +{preset}
                    </button>
                  ))}
                  {maxClose > 0 ? (
                    <button
                      type="button"
                      className={`mobile-ticket__qty-preset mobile-ticket__qty-preset--max tap-target${leg.quantity === maxClose ? " mobile-ticket__qty-preset--active" : ""}`}
                      aria-label={`Set quantity to held ${maxClose} contracts`}
                      aria-pressed={leg.quantity === maxClose}
                      onClick={() => onUpdateLeg(leg.id, { quantity: maxClose })}
                      data-testid={`mobile-order-ticket-leg-${leg.id}-qty-max`}
                    >
                      Max {maxClose}
                    </button>
                  ) : null}
                </div>
              </div>
              );
            })}
          </div>

          {/* 2. Price stepper — after legs */}
          <div className="mobile-ticket__price-section">
            {/* F3: Bid/Mid/Ask are tappable quick-set chips — one tap fills the
                limit input from the live combo quote. */}
            <div className="mobile-ticket__quote">
              <span className="mobile-ticket__quote-label">Bid</span>
              <button
                type="button"
                className="mobile-ticket__quote-chip"
                disabled={signedQuote.bid == null}
                onClick={() => setPriceFromQuote(signedQuote.bid)}
                data-testid="mobile-order-ticket-quote-bid"
                aria-label="Set limit to bid"
              >
                {signedQuote.bid != null ? fmtPrice(Math.abs(signedQuote.bid)) : "—"}
              </button>
              <span className="mobile-ticket__quote-label">Mid</span>
              <button
                type="button"
                className="mobile-ticket__quote-chip"
                disabled={signedQuote.mid == null}
                onClick={() => setPriceFromQuote(signedQuote.mid)}
                data-testid="mobile-order-ticket-quote-mid"
                aria-label="Set limit to mid"
              >
                {signedQuote.mid != null ? fmtPrice(Math.abs(signedQuote.mid)) : "—"}
              </button>
              <span className="mobile-ticket__quote-label">Ask</span>
              <button
                type="button"
                className="mobile-ticket__quote-chip"
                disabled={signedQuote.ask == null}
                onClick={() => setPriceFromQuote(signedQuote.ask)}
                data-testid="mobile-order-ticket-quote-ask"
                aria-label="Set limit to ask"
              >
                {signedQuote.ask != null ? fmtPrice(Math.abs(signedQuote.ask)) : "—"}
              </button>
            </div>

            <ComboSkewPanel
              ticker={ticker}
              legs={legs}
              prices={prices}
              spot={spot}
              riskFreeRate={riskFreeRate}
              compact
            />

            {!isCombo && (
              <div className="mobile-ticket__tif" role="radiogroup" aria-label="Order type">
                {IB_ORDER_TYPES.map((type) => (
                  <button
                    key={type}
                    type="button"
                    role="radio"
                    aria-checked={orderType === type}
                    className={`mobile-ticket__tif-chip${orderType === type ? " mobile-ticket__tif-chip--active" : ""}`}
                    onClick={() => {
                      setOrderType(type);
                      if (isStopOrderType(type)) setTif("GTC");
                      resetConfirm();
                    }}
                    data-testid={`order-type-${type === "STP LMT" ? "stp-lmt" : type.toLowerCase()}`}
                  >
                    {orderTypeLabel(type)}
                  </button>
                ))}
              </div>
            )}

            {!isCombo && isStopOrderType(orderType) && (
              <div className="mobile-ticket__price-row">
                <span className="mobile-ticket__price-label">Stop</span>
                <input
                  className="mobile-ticket__price-input"
                  type="text"
                  inputMode="decimal"
                  value={stopPriceText}
                  onChange={(event) => {
                    setStopPriceText(event.target.value);
                    setPriceManuallySet(true);
                    resetConfirm();
                  }}
                  data-testid="order-stop-price"
                  aria-label="Stop price"
                />
              </div>
            )}

            {(!isStopOrderType(orderType) || orderType === "STP LMT" || isCombo) && (
            <div className="mobile-ticket__price-row">
              <span className="mobile-ticket__price-label">Limit</span>
              <button
                type="button"
                className="mobile-ticket__price-btn"
                onClick={() => adjustPrice(-1)}
                aria-label="Decrease limit price"
                data-testid="mobile-order-ticket-price-down"
              >
                <Minus size={18} aria-hidden />
              </button>
              <input
                className="mobile-ticket__price-input"
                type="text"
                inputMode="decimal"
                value={limitPriceText}
                onChange={(event) => {
                  setLimitPriceText(event.target.value);
                  setPriceManuallySet(true);
                  resetConfirm();
                }}
                data-testid="mobile-order-ticket-price-input"
                aria-label="Limit price"
              />
              <button
                type="button"
                className="mobile-ticket__price-btn"
                onClick={() => adjustPrice(1)}
                aria-label="Increase limit price"
                data-testid="mobile-order-ticket-price-up"
              >
                <Plus size={18} aria-hidden />
              </button>
            </div>
            )}
          </div>

          {/* 3. TIF — after price stepper */}
          <div className="mobile-ticket__tif" role="radiogroup" aria-label="Time in force">
            {(["DAY", "GTC"] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={tif === value}
                className={`mobile-ticket__tif-chip${tif === value ? " mobile-ticket__tif-chip--active" : ""}`}
                onClick={() => {
                  setTif(value);
                  resetConfirm();
                }}
                data-testid={`mobile-order-ticket-tif-${value.toLowerCase()}`}
              >
                {value}
              </button>
            ))}
          </div>

          {/* Footer carries the teaser + Review CTA; this spacer keeps the
              body-scroll content from running under the footer. */}
          <div style={{ height: 8 }} aria-hidden />
        </div>
      )}
    </BottomSheet>
  );
}
