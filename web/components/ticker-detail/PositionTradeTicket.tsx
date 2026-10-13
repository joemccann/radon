"use client";

import ErrorToast from "@/components/ErrorToast";

import { useCallback, useMemo, useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import type { OrdersData, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { legPriceKey, fmtPrice } from "@/lib/positionUtils";
import { fmtSignedPrice } from "@/lib/format";
import { useOrderActionsOptional } from "@/lib/OrderActionsContext";
import { useRealtimePrices } from "@/lib/RealtimePricesContext";
import { checkNakedShortRisk, type NakedShortPortfolio, type OrderPayload } from "@/lib/nakedShortGuard";
import { OrderRiskGate, type OrderRiskState } from "@/lib/order";
import OrderErrorBanner from "@/components/OrderErrorBanner";
import { placeOrderFeedback } from "@/lib/orders/placeOrderFeedback";
import {
  buildPositionTradeOrder,
  closingActionFor,
  heldComboUnits,
  heldPositionTradeUnits,
  resolvePositionTradeTarget,
  overClosesHeldCombo,
  type TradeAction,
  type TradeTarget,
} from "@/lib/order/positionTrade";
import { availablePositionTradeUnits } from "@/lib/order/selectedLegReservations";
import { quoteSubmitGate } from "@/lib/order/quoteSubmitGate";
import {
  type IbOrderType,
  ibPlaceFields,
  isStopOrderType,
  pricesValidForOrderType,
  riskPriceForOrderType,
} from "@/lib/order/stopOrder";
import OrderTypeToggle from "@/components/OrderTypeToggle";
import { OrderQuoteTelemetry } from "@/components/QuoteTelemetry";
import { buildQuoteTelemetryModel, comboQuotePriceData, oldestQuoteTimestamp } from "@/lib/quoteTelemetry";

function toNakedShortPortfolio(portfolio: PortfolioData | null | undefined): NakedShortPortfolio {
  if (!portfolio) return { positions: [] };
  return {
    positions: portfolio.positions.map((p) => ({
      ticker: p.ticker,
      structure_type: p.structure_type,
      contracts: p.contracts,
      direction: p.direction,
      expiry: p.expiry,
      legs: p.legs.map((l) => ({ direction: l.direction, type: l.type, contracts: l.contracts, strike: l.strike })),
    })),
  };
}

/**
 * Net BID/ASK/MID for the combo (signed sum of leg quotes), or one leg's quote.
 * `priceData` is the leg's own live quote (null on a combo, which has no single
 * PriceData) so the telemetry panel reads the traded instrument's session stats.
 */
function useTargetQuote(
  position: PortfolioPosition,
  prices: Record<string, PriceData>,
  target: TradeTarget,
): {
  bid: number | null;
  ask: number | null;
  mid: number | null;
  priceData: PriceData | null;
  /** Stalest leg timestamp behind a combo net, undefined when unknown. */
  asOf?: string;
} {
  return useMemo(() => {
    const selected = target.kind === "legs" ? resolvePositionTradeTarget(position, target) : null;
    if (target.kind === "legs" && !selected) return { bid: null, ask: null, mid: null, priceData: null };
    if (target.kind === "leg" || selected?.legs.length === 1) {
      const leg = target.kind === "leg" ? position.legs[target.index] : selected?.legs[0];
      const key = leg ? legPriceKey(position.ticker, position.expiry, leg) : null;
      const lp = (key ? prices[key] : null) ?? null;
      const bid = lp?.bid ?? null;
      const ask = lp?.ask ?? null;
      return {
        bid,
        ask,
        mid: bid != null && ask != null ? (bid + ask) / 2 : null,
        priceData: lp,
        asOf: lp?.timestamp,
      };
    }
    let netBid = 0;
    let netAsk = 0;
    let ok = true;
    // The combo net is only as fresh as its stalest leg.
    const legQuotes: PriceData[] = [];
    const quotedPosition = selected ?? position;
    const units = heldComboUnits(quotedPosition);
    for (const leg of quotedPosition.legs) {
      const key = legPriceKey(position.ticker, position.expiry, leg);
      const lp = key ? prices[key] : null;
      if (!lp || lp.bid == null || lp.ask == null) { ok = false; break; }
      legQuotes.push(lp);
      const ratio = leg.contracts / units;
      if (leg.direction === "LONG") {
        netBid += ratio * lp.bid;
        netAsk += ratio * lp.ask;
      } else {
        netBid -= ratio * lp.ask;
        netAsk -= ratio * lp.bid;
      }
    }
    if (!ok) return { bid: null, ask: null, mid: null, priceData: null };
    const bid = Math.min(netBid, netAsk);
    const ask = Math.max(netBid, netAsk);
    return {
      bid,
      ask,
      mid: (bid + ask) / 2,
      priceData: null,
      asOf: oldestQuoteTimestamp(legQuotes),
    };
  }, [position, prices, target]);
}

type PositionTradeTicketProps = {
  position: PortfolioPosition;
  prices: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
  orders?: OrdersData | null;
  target: TradeTarget;
  onClose: () => void;
  onOrderPlaced?: () => void;
};

export default function PositionTradeTicket({
  position,
  prices,
  portfolio,
  orders,
  target,
  onClose,
  onOrderPlaced,
}: PositionTradeTicketProps) {
  const orderActions = useOrderActionsOptional();
  const { connected, ibConnected, marketDataDegraded } = useRealtimePrices();
  const feedConnected = connected && ibConnected && !marketDataDegraded;
  const selectedOnly = target.kind === "legs";
  const targetPosition = useMemo(() => resolvePositionTradeTarget(position, target), [position, target]);
  const isComboTarget = target.kind === "combo" || (selectedOnly && (targetPosition?.legs.length ?? 0) > 1);
  // Combo quantity counts BAG UNITS, not contracts. `position.contracts` is the
  // first LONG leg's count (ib_sync.py), so defaulting to it against real
  // per-leg ratios would over-trade a ratio structure by the ratio itself.
  const heldQty = heldPositionTradeUnits(position, target);
  const availableQty = selectedOnly ? availablePositionTradeUnits(position, target, orders) : heldQty;

  const [action, setAction] = useState<TradeAction>(() => closingActionFor(position, target));
  const [quantity, setQuantity] = useState(String(heldQty));
  const [limitPrice, setLimitPrice] = useState("");
  const [orderType, setOrderType] = useState<IbOrderType>("LMT");
  const [stopPrice, setStopPrice] = useState("");
  const [tif, setTif] = useState<"DAY" | "GTC">("DAY");
  const [confirmStep, setConfirmStep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [riskState, setRiskState] = useState<OrderRiskState | null>(null);

  const reset = () => { setConfirmStep(false); setRiskState(null); };

  const { bid, ask, mid, asOf, priceData: legPriceData } = useTargetQuote(position, prices, target);

  const comboQuoteModel = useMemo(() => {
    if (!isComboTarget) return null;
    if (bid == null && ask == null) return null;
    return buildQuoteTelemetryModel(
      comboQuotePriceData({ symbol: position.ticker, bid, ask, last: mid, timestamp: asOf }),
    );
  }, [isComboTarget, position.ticker, bid, ask, mid, asOf]);

  const parsedQty = Number(quantity);
  const parsedPrice = parseFloat(limitPrice);
  const parsedStop = parseFloat(stopPrice);
  const priceValid =
    isComboTarget
      ? Number.isFinite(parsedPrice) && parsedPrice !== 0
      : pricesValidForOrderType({ orderType, limitPrice: parsedPrice, stopPrice: parsedStop });
  const overClosesCombo = target.kind === "combo" && overClosesHeldCombo(action, parsedQty, heldQty);
  const overClosesSelection = selectedOnly && availableQty != null && parsedQty > availableQty;
  const isValid = Number.isSafeInteger(parsedQty) && parsedQty > 0 && priceValid && !overClosesCombo
    && (!selectedOnly || (targetPosition != null && availableQty != null && !overClosesSelection));

  const underlyingSpot = prices[position.ticker]?.last ?? null;

  const riskExecutionPrice = isComboTarget
    ? parsedPrice
    : riskPriceForOrderType(orderType, parsedPrice, parsedStop);
  const built = useMemo(
    () =>
      isValid
        ? buildPositionTradeOrder({ position, target, action, quantity: parsedQty, limitPrice: riskExecutionPrice, tif, quote: { bid, ask }, underlyingSpot })
        : null,
    [isValid, position, target, action, parsedQty, riskExecutionPrice, tif, bid, ask, underlyingSpot],
  );

  const nakedShortWarning = useMemo(() => {
    if (!built) return null;
    const res = checkNakedShortRisk(built.payload as OrderPayload, toNakedShortPortfolio(portfolio));
    return res.allowed ? null : res.reason ?? "Order blocked: naked short exposure";
  }, [built, portfolio]);

  const subjectLabel = useMemo(() => {
    if (target.kind === "combo") return position.structure;
    if (target.kind === "legs") return `${position.ticker} · ${targetPosition?.legs.length ?? 0} selected legs`;
    const leg = position.legs[target.index];
    return `${leg.direction} ${leg.type} $${leg.strike}`;
  }, [target, position, targetPosition]);

  const previewUnits = Number.isSafeInteger(parsedQty) && parsedQty > 0 ? parsedQty : 0;
  const selectedLegIndices = selectedOnly ? target.indices : [];
  const closeContracts = (index: number) => selectedLegIndices.includes(index) && heldQty > 0
    ? previewUnits * position.legs[index].contracts / heldQty : 0;
  const remainingLegs = position.legs.map((leg, index) => ({ ...leg, contracts: Math.max(0, leg.contracts - closeContracts(index)) }))
    .filter((leg) => leg.contracts > 0);

  const okToSubmit = riskState?.okToSubmit === true;
  const quoteGate = quoteSubmitGate({ quoteTimestamp: asOf, feedConnected });

  const handlePlace = useCallback(async () => {
    if (!isValid) return;
    if (!confirmStep) { setConfirmStep(true); return; }
    if (!built) return;
    if (!okToSubmit) return;
    // Re-evaluate age on the click: a ticket can sit idle after review.
    const currentQuoteGate = quoteSubmitGate({ quoteTimestamp: asOf, feedConnected });
    if (!currentQuoteGate.open) { setError(currentQuoteGate.reason); return; }
    setLoading(true);
    setError(null);
    try {
      const guard = checkNakedShortRisk(built.payload as OrderPayload, toNakedShortPortfolio(portfolio));
      if (!guard.allowed) {
        setError(guard.reason ?? "Order blocked: naked short exposure");
        setLoading(false);
        return;
      }
      const extra = !isComboTarget ? ibPlaceFields(orderType, parsedPrice, parsedStop) : {};
      const payload = { ...built.payload, ...extra };
      if (orderType === "STP") delete payload.limitPrice;
      const res = await fetch("/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || "Order placement failed");
      } else {
        const feedback = placeOrderFeedback(
          json,
          `${action} ${parsedQty}x ${position.ticker} ${subjectLabel} @ ${fmtSignedPrice(riskExecutionPrice)}`,
        );
        orderActions?.pushNotification({ type: feedback.tone, message: feedback.message });
        setConfirmStep(false);
        onOrderPlaced?.();
        onClose();
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [isValid, confirmStep, built, okToSubmit, asOf, feedConnected, portfolio, orderActions, action, parsedQty, position.ticker, subjectLabel, riskExecutionPrice, onOrderPlaced, onClose, isComboTarget, orderType, parsedPrice, parsedStop]);

  const closingHint =
    built?.isClosing === false
      ? target.kind === "leg"
        ? "Opens / adds exposure on this leg."
        : "Adds to the combo."
      : null;

  return (
    <div className="position-trade-ticket order-form" data-testid="position-trade-ticket">
      <div className="position-trade-head">
        <div>
          <div className="position-trade-eyebrow">{selectedOnly ? "Close Selected Legs" : target.kind === "combo" ? "Trade Combo" : "Trade Leg"}</div>
          <div className="position-trade-subject">{subjectLabel}</div>
        </div>
        <button type="button" className="position-trade-close" onClick={onClose} aria-label="Cancel trade">
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      {selectedOnly && (
        <div className="position-trade-leg-review">
          <div>
            <h3>Selected legs to close</h3>
            <ul data-testid="position-trade-selected-legs">
              {selectedLegIndices.map((index) => {
                const leg = position.legs[index];
                if (!leg) return null;
                return <li key={index}>{leg.direction === "LONG" ? "Sell to Close" : "Buy to Close"} {closeContracts(index)}x {leg.type} ${leg.strike} · {leg.expiry ?? position.expiry}</li>;
              })}
            </ul>
          </div>
          <div>
            <h3>Remaining after close</h3>
            <ul data-testid="position-trade-remaining-legs">
              {remainingLegs.length === 0 ? <li>No remaining legs in this position</li>
                : remainingLegs.map((leg, index) => <li key={index}>{leg.direction} {leg.contracts}x {leg.type} ${leg.strike} · {leg.expiry ?? position.expiry}</li>)}
            </ul>
          </div>
        </div>
      )}

      <OrderQuoteTelemetry
        priceData={legPriceData}
        model={comboQuoteModel}
        label={subjectLabel}
      />

      <div className="order-field">
        <label className="order-label">Action</label>
        {selectedOnly ? <span className="position-trade-hint">{isComboTarget ? "Close the selected legs together" : action === "BUY" ? "Buy to Close" : "Sell to Close"}</span> : <div className="order-action-buttons">
          <button
            className={`order-action-btn ${action === "BUY" ? "order-action-active order-action-buy" : ""}`}
            onClick={() => { setAction("BUY"); reset(); }}
          >
            BUY
          </button>
          <button
            className={`order-action-btn ${action === "SELL" ? "order-action-active order-action-sell" : ""}`}
            onClick={() => { setAction("SELL"); reset(); }}
          >
            SELL
          </button>
        </div>}
        {closingHint && <span className="position-trade-hint">{closingHint}</span>}
      </div>

      {!isComboTarget && (
        <OrderTypeToggle
          value={orderType}
          onChange={(next) => {
            setOrderType(next);
            if (isStopOrderType(next)) setTif("GTC");
            reset();
          }}
        />
      )}

      <div className="order-field">
        <label className="order-label">Quantity{isComboTarget ? " (combos)" : " (contracts)"}</label>
        <input
          className="order-input"
          type="number"
          min="1"
          max={selectedOnly ? availableQty ?? 0 : undefined}
          step="1"
          value={quantity}
          onChange={(e) => { setQuantity(e.target.value); reset(); }}
          placeholder={String(heldQty)}
          data-testid="position-trade-qty"
        />
        <span className="position-trade-hint">Held: {heldQty}</span>
        {selectedOnly && <span className="position-trade-hint">{availableQty == null ? "Working orders resolving" : `Available to close: ${availableQty}`}</span>}
      </div>

      {!isComboTarget && isStopOrderType(orderType) && (
        <div className="order-field">
          <label className="order-label">Stop Price</label>
          <div className="modify-price-input-row">
            <span className="modify-price-prefix">$</span>
            <input
              className="modify-price-input"
              type="number"
              step="0.01"
              value={stopPrice}
              onChange={(e) => { setStopPrice(e.target.value); reset(); }}
              placeholder="0.00"
              data-testid="order-stop-price"
            />
          </div>
          <div className="modify-quick-buttons">
            <button className="btn-quick" disabled={bid == null} onClick={() => { if (bid != null) { setStopPrice(bid.toFixed(2)); reset(); } }}>BID {bid != null ? fmtPrice(bid) : "--"}</button>
            <button className="btn-quick" disabled={mid == null} onClick={() => { if (mid != null) { setStopPrice(mid.toFixed(2)); reset(); } }}>MID {mid != null ? fmtPrice(mid) : "--"}</button>
            <button className="btn-quick" disabled={ask == null} onClick={() => { if (ask != null) { setStopPrice(ask.toFixed(2)); reset(); } }}>ASK {ask != null ? fmtPrice(ask) : "--"}</button>
          </div>
        </div>
      )}

      {(isComboTarget || orderType !== "STP") && (
      <div className="order-field">
        <label className="order-label">{isComboTarget ? "Net Limit" : "Limit Price"}</label>
        <div className="modify-price-input-row">
          <span className="modify-price-prefix">$</span>
          <input
            className="modify-price-input"
            type="number"
            step="0.01"
            value={limitPrice}
            onChange={(e) => { setLimitPrice(e.target.value); reset(); }}
            placeholder="0.00"
            data-testid="position-trade-limit"
          />
        </div>
        <div className="modify-quick-buttons">
          <button className="btn-quick" disabled={bid == null} onClick={() => { if (bid != null) { setLimitPrice(bid.toFixed(2)); reset(); } }}>BID {bid != null ? fmtPrice(bid) : "--"}</button>
          <button className="btn-quick" disabled={mid == null} onClick={() => { if (mid != null) { setLimitPrice(mid.toFixed(2)); reset(); } }}>MID {mid != null ? fmtPrice(mid) : "--"}</button>
          <button className="btn-quick" disabled={ask == null} onClick={() => { if (ask != null) { setLimitPrice(ask.toFixed(2)); reset(); } }}>ASK {ask != null ? fmtPrice(ask) : "--"}</button>
        </div>
      </div>
      )}

      <div className="order-field">
        <label className="order-label">Time in Force</label>
        <div className="order-action-buttons">
          <button className={`order-action-btn ${tif === "DAY" ? "order-action-active" : ""}`} onClick={() => { setTif("DAY"); reset(); }}>DAY</button>
          <button className={`order-action-btn ${tif === "GTC" ? "order-action-active" : ""}`} onClick={() => { setTif("GTC"); reset(); }}>GTC</button>
        </div>
      </div>

      {nakedShortWarning && (
        <div className="order-error" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <AlertTriangle size={14} />
          <span>{nakedShortWarning}</span>
        </div>
      )}

      {overClosesCombo && (
        <ErrorToast message={`Quantity exceeds held combo units (${heldQty}).`} />
      )}
      {overClosesSelection && <ErrorToast message={`Quantity exceeds available selected units (${availableQty}).`} />}

      <OrderErrorBanner error={error} />
      {confirmStep && !quoteGate.open && <ErrorToast message={quoteGate.reason!} />}

      {confirmStep && (
        <OrderRiskGate
          input={built?.riskInput ?? null}
          portfolio={portfolio}
          surface="position-trade"
          variant="info"
          onState={setRiskState}
        />
      )}

      <div className="order-submit">
        {confirmStep ? (
          <div className="order-confirm-row">
            <button className="btn-secondary" onClick={reset} disabled={loading}>Back</button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!isValid || loading || !!nakedShortWarning || !okToSubmit || !quoteGate.open}
            >
              {loading ? "Placing..." : "Confirm Order"}
            </button>
          </div>
        ) : (
          <button
            className="btn-primary"
            onClick={handlePlace}
            disabled={!isValid || loading || !!nakedShortWarning}
            style={{ width: "100%" }}
          >
            Review Order
          </button>
        )}
      </div>
    </div>
  );
}
