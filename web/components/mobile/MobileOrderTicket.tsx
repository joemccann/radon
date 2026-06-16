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
import { OrderRiskGate, type OrderRiskInput } from "@/lib/order";
import BuySellRow from "./BuySellRow";
import BottomSheet from "./BottomSheet";

type MobileOrderTicketProps = {
  open: boolean;
  ticker: string;
  legs: OrderLeg[];
  prices: Record<string, PriceData>;
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

const PRICE_INCREMENT = 0.05;

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
  portfolio = null,
  onClose,
  onRemoveLeg,
  onUpdateLeg,
  onClearLegs,
}: MobileOrderTicketProps) {
  const [tif, setTif] = useState<"DAY" | "GTC">("DAY");
  const [limitPriceText, setLimitPriceText] = useState<string>("");
  const [priceManuallySet, setPriceManuallySet] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

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

  // Reset manual-set when leg structure changes (action/right/strike differ).
  const structureKey = useMemo(
    () => legs.map((l) => `${l.action}-${l.right}-${l.strike}-${l.expiry}-${l.quantity}`).join("|"),
    [legs],
  );
  const lastStructureRef = useMemo(() => ({ current: "" }), []);
  useEffect(() => {
    if (structureKey === lastStructureRef.current) return;
    lastStructureRef.current = structureKey;
    setPriceManuallySet(false);
  }, [structureKey, lastStructureRef]);

  const parsedPrice = parseFloat(limitPriceText);
  const isValidPrice = !isNaN(parsedPrice) && (isCombo ? parsedPrice !== 0 : parsedPrice > 0);
  const signedLimitPrice = Number.isFinite(parsedPrice)
    ? isDebit === null
      ? parsedPrice
      : isDebit
        ? Math.abs(parsedPrice)
        : -Math.abs(parsedPrice)
    : NaN;

  // Build the chokepoint input — every order surface that displays risk math
  // MUST route through `<OrderRiskGate>`. Mobile was a structural gap before
  // (commit 6a14278 landed desktop; this is the mobile equivalent). Same
  // single-leg credit-sign rule applies: a SELL leg is structurally a credit
  // regardless of whether live WS bid/ask have populated.
  const riskInput: OrderRiskInput | null = useMemo(() => {
    if (!isValidPrice || legs.length === 0) return null;
    const totalCost = parsedPrice * totalQty * 100;
    const description = `${structure || "Option"} @ ${fmtPrice(parsedPrice)}`;
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
    };
  }, [isValidPrice, parsedPrice, totalQty, structure, isDebit, isCombo, legs, normalizedOrder, ticker]);

  const adjustPrice = (delta: number) => {
    const next = (parseFloat(limitPriceText) || signedQuote.mid || 0) + delta;
    setLimitPriceText(next.toFixed(2));
    setPriceManuallySet(true);
  };

  const handleSubmit = async () => {
    if (!isValidPrice || legs.length === 0) return;
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
            limitPrice: parsedPrice,
            tif,
            expiry: normalizeOptionExpiry(legs[0].expiry) ?? legs[0].expiry,
            strike: legs[0].strike,
            right: legs[0].right === "C" ? "CALL" : "PUT",
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

  const directionLabel = netIsCredit ? "SELL TO OPEN" : "BUY TO OPEN";
  const netLabel = netIsCredit
    ? `NET CREDIT ${signedLimitPrice && Number.isFinite(signedLimitPrice) ? fmtPrice(Math.abs(signedLimitPrice)) : ""}`
    : `NET DEBIT ${signedLimitPrice && Number.isFinite(signedLimitPrice) ? fmtPrice(Math.abs(signedLimitPrice)) : ""}`;

  return (
    <BottomSheet
      open
      onClose={onClose}
      title={`${ticker.toUpperCase()} · ${structure || "Order"}`}
      testId="mobile-order-ticket"
      maxHeight="82vh"
      footer={
        <>
          {error ? <div className="mobile-ticket__error" data-testid="mobile-order-ticket-error">{error}</div> : null}
          {success ? <div className="mobile-ticket__success" data-testid="mobile-order-ticket-success">{success}</div> : null}
          {/* Risk summary pinned just above the footer submit */}
          <div className="mobile-ticket__risk" data-testid="mobile-order-ticket-risk">
            <OrderRiskGate
              input={riskInput}
              portfolio={portfolio}
              surface="mobile-ticket"
              variant="info"
            />
          </div>
          <button
            type="button"
            className={`mobile-ticket__submit${submitColorClass ? ` ${submitColorClass}` : ""}`}
            onClick={handleSubmit}
            disabled={!isValidPrice || submitting || legs.length === 0}
            data-testid="mobile-order-ticket-submit"
          >
            {submitting ? (
              "Placing..."
            ) : (
              <span className="mobile-ticket__submit-inner">
                <span className="mobile-ticket__submit-direction">{directionLabel}</span>
                <span className="mobile-ticket__submit-net">{netLabel}</span>
              </span>
            )}
          </button>
        </>
      }
    >
      <div className="mobile-ticket">
        {/* 1. Legs — using BuySellRow for visual accent + 44px touch targets */}
        <div className="mobile-ticket__legs" data-testid="mobile-order-ticket-legs">
          {legs.map((leg) => (
            <div key={leg.id} className="mobile-ticket__leg-row">
              <BuySellRow
                side={leg.action}
                label={legDescription(leg)}
                price={legMidPrice(leg, prices)}
                sub={`${leg.right === "C" ? "Call" : "Put"} · ${formatExpiry(leg.expiry)}`}
              />
              <div className="mobile-ticket__leg-controls">
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
            </div>
          ))}
        </div>

        {/* 2. Price stepper — after legs */}
        <div className="mobile-ticket__price-section">
          <div className="mobile-ticket__quote">
            <span className="mobile-ticket__quote-label">Bid</span>
            <span className="mobile-ticket__quote-value">{signedQuote.bid != null ? fmtPrice(Math.abs(signedQuote.bid)) : "—"}</span>
            <span className="mobile-ticket__quote-label">Mid</span>
            <span className="mobile-ticket__quote-value">{signedQuote.mid != null ? fmtPrice(Math.abs(signedQuote.mid)) : "—"}</span>
            <span className="mobile-ticket__quote-label">Ask</span>
            <span className="mobile-ticket__quote-value">{signedQuote.ask != null ? fmtPrice(Math.abs(signedQuote.ask)) : "—"}</span>
          </div>

          <div className="mobile-ticket__price-row">
            <span className="mobile-ticket__price-label">Limit</span>
            <button
              type="button"
              className="mobile-ticket__price-btn"
              onClick={() => adjustPrice(-PRICE_INCREMENT)}
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
              }}
              data-testid="mobile-order-ticket-price-input"
              aria-label="Limit price"
            />
            <button
              type="button"
              className="mobile-ticket__price-btn"
              onClick={() => adjustPrice(PRICE_INCREMENT)}
              aria-label="Increase limit price"
              data-testid="mobile-order-ticket-price-up"
            >
              <Plus size={18} aria-hidden />
            </button>
          </div>
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
              onClick={() => setTif(value)}
              data-testid={`mobile-order-ticket-tif-${value.toLowerCase()}`}
            >
              {value}
            </button>
          ))}
        </div>

        {/* Risk summary is now pinned in the footer (above submit).
            This empty spacer keeps the body-scroll content from running
            under the footer when the body is near the bottom of the sheet. */}
        <div style={{ height: 8 }} aria-hidden />
      </div>
    </BottomSheet>
  );
}
