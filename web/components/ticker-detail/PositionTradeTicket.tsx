"use client";

import { useCallback, useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { legPriceKey, fmtPrice } from "@/lib/positionUtils";
import { fmtSignedPrice } from "@/lib/format";
import { useOrderActionsOptional } from "@/lib/OrderActionsContext";
import { checkNakedShortRisk, type NakedShortPortfolio, type OrderPayload } from "@/lib/nakedShortGuard";
import { OrderRiskGate, type OrderRiskState } from "@/lib/order";
import OrderErrorBanner from "@/components/OrderErrorBanner";
import { placeOrderFeedback } from "@/lib/orders/placeOrderFeedback";
import {
  buildPositionTradeOrder,
  closingActionFor,
  heldComboUnits,
  overClosesHeldCombo,
  type TradeAction,
  type TradeTarget,
} from "@/lib/order/positionTrade";
import {
  type IbOrderType,
  ibPlaceFields,
  isStopOrderType,
  pricesValidForOrderType,
  riskPriceForOrderType,
} from "@/lib/order/stopOrder";
import OrderTypeToggle from "@/components/OrderTypeToggle";

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

/** Net BID/ASK/MID for the combo (signed sum of leg quotes), or one leg's quote. */
function useTargetQuote(
  position: PortfolioPosition,
  prices: Record<string, PriceData>,
  target: TradeTarget,
): { bid: number | null; ask: number | null; mid: number | null } {
  return useMemo(() => {
    if (target.kind === "leg") {
      const leg = position.legs[target.index];
      const key = leg ? legPriceKey(position.ticker, position.expiry, leg) : null;
      const lp = key ? prices[key] : null;
      const bid = lp?.bid ?? null;
      const ask = lp?.ask ?? null;
      return { bid, ask, mid: bid != null && ask != null ? (bid + ask) / 2 : null };
    }
    let netBid = 0;
    let netAsk = 0;
    let ok = true;
    for (const leg of position.legs) {
      const key = legPriceKey(position.ticker, position.expiry, leg);
      const lp = key ? prices[key] : null;
      if (!lp || lp.bid == null || lp.ask == null) { ok = false; break; }
      const sign = leg.direction === "LONG" ? 1 : -1;
      netBid += sign * lp.bid;
      netAsk += sign * lp.ask;
    }
    if (!ok) return { bid: null, ask: null, mid: null };
    const bid = Math.min(netBid, netAsk);
    const ask = Math.max(netBid, netAsk);
    return { bid, ask, mid: (bid + ask) / 2 };
  }, [position, prices, target]);
}

type PositionTradeTicketProps = {
  position: PortfolioPosition;
  prices: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
  target: TradeTarget;
  onClose: () => void;
  onOrderPlaced?: () => void;
};

export default function PositionTradeTicket({
  position,
  prices,
  portfolio,
  target,
  onClose,
  onOrderPlaced,
}: PositionTradeTicketProps) {
  const orderActions = useOrderActionsOptional();
  // Combo quantity counts BAG UNITS, not contracts. `position.contracts` is the
  // first LONG leg's count (ib_sync.py), so defaulting to it against real
  // per-leg ratios would over-trade a ratio structure by the ratio itself.
  const heldQty =
    target.kind === "leg"
      ? position.legs[target.index]?.contracts ?? 1
      : heldComboUnits(position);

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

  const reset = () => setConfirmStep(false);

  const { bid, ask, mid } = useTargetQuote(position, prices, target);

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const parsedStop = parseFloat(stopPrice);
  const priceValid =
    target.kind === "combo"
      ? Number.isFinite(parsedPrice) && parsedPrice !== 0
      : pricesValidForOrderType({ orderType, limitPrice: parsedPrice, stopPrice: parsedStop });
  const overClosesCombo = target.kind === "combo" && overClosesHeldCombo(action, parsedQty, heldQty);
  const isValid = !isNaN(parsedQty) && parsedQty > 0 && priceValid && !overClosesCombo;

  const underlyingSpot = prices[position.ticker]?.last ?? null;

  const riskExecutionPrice = target.kind === "combo"
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
    const leg = position.legs[target.index];
    return `${leg.direction} ${leg.type} $${leg.strike}`;
  }, [target, position]);

  const okToSubmit = riskState?.okToSubmit !== false; // null (pre-confirm) allowed

  const handlePlace = useCallback(async () => {
    if (!isValid) return;
    if (!confirmStep) { setConfirmStep(true); return; }
    if (!built) return;
    setLoading(true);
    setError(null);
    try {
      const guard = checkNakedShortRisk(built.payload as OrderPayload, toNakedShortPortfolio(portfolio));
      if (!guard.allowed) {
        setError(guard.reason ?? "Order blocked: naked short exposure");
        setLoading(false);
        return;
      }
      const extra = target.kind === "leg" ? ibPlaceFields(orderType, parsedPrice, parsedStop) : {};
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
  }, [isValid, confirmStep, built, portfolio, orderActions, action, parsedQty, position.ticker, subjectLabel, riskExecutionPrice, onOrderPlaced, onClose, target.kind, orderType, parsedPrice, parsedStop]);

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
          <div className="position-trade-eyebrow">{target.kind === "combo" ? "Trade Combo" : "Trade Leg"}</div>
          <div className="position-trade-subject">{subjectLabel}</div>
        </div>
        <button type="button" className="position-trade-close" onClick={onClose} aria-label="Cancel trade">
          esc ✕
        </button>
      </div>

      <div className="order-field">
        <label className="order-label">Action</label>
        <div className="order-action-buttons">
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
        </div>
        {closingHint && <span className="position-trade-hint">{closingHint}</span>}
      </div>

      {target.kind === "leg" && (
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
        <label className="order-label">Quantity{target.kind === "leg" ? " (contracts)" : " (combos)"}</label>
        <input
          className="order-input"
          type="number"
          min="1"
          step="1"
          value={quantity}
          onChange={(e) => { setQuantity(e.target.value); reset(); }}
          placeholder={String(heldQty)}
          data-testid="position-trade-qty"
        />
        <span className="position-trade-hint">Held: {heldQty}</span>
      </div>

      {target.kind === "leg" && isStopOrderType(orderType) && (
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

      {(target.kind === "combo" || orderType !== "STP") && (
      <div className="order-field">
        <label className="order-label">{target.kind === "combo" ? "Net Limit" : "Limit Price"}</label>
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
          <button className={`order-action-btn ${tif === "DAY" ? "order-action-active" : ""}`} onClick={() => setTif("DAY")}>DAY</button>
          <button className={`order-action-btn ${tif === "GTC" ? "order-action-active" : ""}`} onClick={() => setTif("GTC")}>GTC</button>
        </div>
      </div>

      {nakedShortWarning && (
        <div className="order-error" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <AlertTriangle size={14} />
          <span>{nakedShortWarning}</span>
        </div>
      )}

      {overClosesCombo && (
        <div className="order-error">
          Quantity exceeds held combo units ({heldQty}).
        </div>
      )}

      <OrderErrorBanner error={error} />

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
            <button className="btn-secondary" onClick={() => setConfirmStep(false)} disabled={loading}>Back</button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!isValid || loading || !!nakedShortWarning || !okToSubmit}
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
