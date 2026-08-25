"use client";

import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import type { PortfolioData } from "@/lib/types";
import {
  OrderRiskGate,
  type OrderRiskInput,
  type OrderRiskState,
  useOrderRisk,
} from "@/lib/order";
import OrderErrorBanner from "./OrderErrorBanner";
import OrderTypeToggle from "./OrderTypeToggle";
import { OrderQuoteTelemetry } from "./QuoteTelemetry";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  type IbOrderType,
  isStopOrderType,
  pricesValidForOrderType,
} from "@/lib/order/stopOrder";
import {
  placeOrderFeedback,
  type PlaceOrderFeedback,
  type PlaceOrderTone,
} from "@/lib/orders/placeOrderFeedback";

export type SingleLegOrderAction = "BUY" | "SELL";
export type SingleLegOrderTif = "DAY" | "GTC";

export function singleLegSubmitPermitted(
  formValid: boolean,
  riskState: OrderRiskState | null,
): boolean {
  return formValid && riskState?.okToSubmit === true;
}

/**
 * Presentational single-leg order ticket. Owns the BUY/SELL toggle,
 * quantity field, limit-price block (with BID/MID/ASK quick buttons),
 * TIF toggle, error/success rendering, the two-step confirm state
 * machine, and the slot where the caller's `<OrderRiskGate>` renders.
 *
 * Everything that differs between an option ticket and a stock ticket is
 * injected via props: live bid/mid/ask, the quantity controlled value +
 * setter, the placeholder/label copy, the risk-gate node (caller builds
 * the `OrderRiskInput` and renders `<OrderRiskGate>`), the submit-payload
 * builder, and a validity flag.
 *
 * Markup + classnames are identical to the previous in-line forms so CSS
 * and E2E selectors are unaffected.
 */
export type SingleLegOrderTicketProps = {
  /** Initial action; the toggle is owned internally afterwards. */
  defaultAction: SingleLegOrderAction;
  /** Initial TIF; the toggle is owned internally afterwards. */
  defaultTif: SingleLegOrderTif;
  /** Controlled quantity value (caller owns the string state). */
  quantity: string;
  onQuantityChange: (value: string) => void;
  quantityPlaceholder: string;
  /** Live quote sides driving the quick buttons + (optionally) labels. */
  bid: number | null;
  mid: number | null;
  ask: number | null;
  /**
   * Full live quote for the instrument being traded. When present the ticket
   * renders the shared nine-field `<OrderQuoteTelemetry>` block above the
   * Action field; omitted, no quote block is rendered at all.
   */
  priceData?: PriceData | null;
  /** Instrument caption for the telemetry block (e.g. "AAPL $170 Call 2026-08-28"). */
  quoteLabel?: string;
  /** When true the quick buttons append the price (e.g. "BID 1.23"). */
  showQuickButtonPrices?: boolean;
  /** Whether the current form state is submittable. */
  isValid: boolean;
  /** Controlled limit-price value mirror — caller reads it to build risk + payload. */
  limitPrice: string;
  onLimitPriceChange: (value: string) => void;
  /** Optional controlled stop ticket. Omitted → the ticket owns type + stop. */
  orderType?: IbOrderType;
  onOrderTypeChange?: (value: IbOrderType) => void;
  stopPrice?: string;
  onStopPriceChange?: (value: string) => void;
  /** Canonical risk input and coverage state. The ticket owns the gate so a
   * caller cannot render a warning while bypassing its submit permit. */
  riskInput: OrderRiskInput | null;
  portfolio: PortfolioData | null | undefined;
  riskSurface: string;
  riskPaperMode?: boolean;
  onRiskPaperModeChange?: (next: boolean) => void;
  /** Optional header rendered above the Action field (e.g. "STOCK ORDER"). */
  header?: ReactNode;
  /**
   * Placement endpoint. Defaults to the live IB path `/api/orders/place`.
   * Paper-mode surfaces pass `/api/paper/place` (resolved from the
   * OrderRiskGate Paper toggle via `resolvePlacementTarget`).
   */
  placeUrl?: string;
  /** Builds the placement body from the resolved ticket state. */
  buildPayload: (state: {
    action: SingleLegOrderAction;
    quantity: number;
    limitPrice: number;
    tif: SingleLegOrderTif;
    orderType: IbOrderType;
    stopPrice: number;
  }) => Record<string, unknown>;
  /** Human-readable success line, also surfaced to the optional toast sink. */
  buildSuccessMessage: (state: {
    action: SingleLegOrderAction;
    quantity: number;
    limitPrice: number;
    orderType: IbOrderType;
    stopPrice: number;
  }) => string;
  /** Notified of the live action so callers can build the right risk input. */
  onActionChange?: (action: SingleLegOrderAction) => void;
  /** Notified of the live TIF (callers rarely need it). */
  onTifChange?: (tif: SingleLegOrderTif) => void;
  /**
   * Optional toast sink. When provided, a settled placement routes through
   * it; the inline `.order-success` block is still rendered too unless
   * `suppressInlineSuccess` is set. `tone` is "warning" for a suppressed
   * duplicate submit (the order was NOT sent again), "success" otherwise.
   */
  onSuccessToast?: (message: string, tone: PlaceOrderTone) => void;
  /** When true, success is routed only to the toast sink, not inline. */
  suppressInlineSuccess?: boolean;
  /** Extra class on the outer `.order-form`. */
  className?: string;
  /** Inline style on the outer `.order-form` (e.g. marginTop). */
  style?: React.CSSProperties;
};

export default function SingleLegOrderTicket({
  defaultAction,
  defaultTif,
  quantity,
  onQuantityChange,
  quantityPlaceholder,
  bid,
  mid,
  ask,
  priceData,
  quoteLabel,
  showQuickButtonPrices = false,
  isValid,
  limitPrice,
  onLimitPriceChange,
  orderType: orderTypeProp,
  onOrderTypeChange,
  stopPrice: stopPriceProp,
  onStopPriceChange,
  riskInput,
  portfolio,
  riskSurface,
  riskPaperMode,
  onRiskPaperModeChange,
  header,
  placeUrl = "/api/orders/place",
  buildPayload,
  buildSuccessMessage,
  onActionChange,
  onTifChange,
  onSuccessToast,
  suppressInlineSuccess = false,
  className,
  style,
}: SingleLegOrderTicketProps) {
  const [action, setAction] = useState<SingleLegOrderAction>(defaultAction);
  const [tif, setTif] = useState<SingleLegOrderTif>(defaultTif);
  const [innerOrderType, setInnerOrderType] = useState<IbOrderType>("LMT");
  const [innerStopPrice, setInnerStopPrice] = useState("");
  const orderType = orderTypeProp ?? innerOrderType;
  const stopPrice = stopPriceProp ?? innerStopPrice;
  const setOrderType = (next: IbOrderType) => {
    setInnerOrderType(next);
    onOrderTypeChange?.(next);
  };
  const setStopPrice = (next: string) => {
    setInnerStopPrice(next);
    onStopPriceChange?.(next);
  };
  const [confirmStep, setConfirmStep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<PlaceOrderFeedback | null>(null);

  const selectAction = useCallback(
    (next: SingleLegOrderAction) => {
      setAction(next);
      setConfirmStep(false);
      onActionChange?.(next);
    },
    [onActionChange],
  );

  const selectTif = useCallback(
    (next: SingleLegOrderTif) => {
      setTif(next);
      onTifChange?.(next);
    },
    [onTifChange],
  );

  const setQuickPrice = useCallback(
    (value: number | null) => {
      if (value == null) return;
      if (isStopOrderType(orderType)) {
        setStopPrice(value.toFixed(2));
      } else {
        onLimitPriceChange(value.toFixed(2));
      }
      setConfirmStep(false);
    },
    [onLimitPriceChange, orderType],
  );

  const selectOrderType = (next: IbOrderType) => {
    setOrderType(next);
    if (isStopOrderType(next)) {
      setTif("GTC");
      onTifChange?.("GTC");
    }
    setConfirmStep(false);
  };

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const parsedStop = parseFloat(stopPrice);
  const typePriceValid = pricesValidForOrderType({
    orderType,
    limitPrice: parsedPrice,
    stopPrice: parsedStop,
  });
  const formValid = isValid && typePriceValid && !isNaN(parsedQty) && parsedQty > 0;
  const riskState = useOrderRisk(riskInput, portfolio);
  const canSubmit = singleLegSubmitPermitted(formValid, riskState);

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      if (!formValid) return;
      setConfirmStep(true);
      return;
    }
    if (!canSubmit) return;

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const res = await fetch(placeUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          buildPayload({
            action,
            quantity: parsedQty,
            limitPrice: parsedPrice,
            tif,
            orderType,
            stopPrice: parsedStop,
          }),
        ),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || "Order placement failed");
      } else {
        const feedback = placeOrderFeedback(
          json,
          buildSuccessMessage({
            action,
            quantity: parsedQty,
            limitPrice: parsedPrice,
            orderType,
            stopPrice: parsedStop,
          }),
        );
        if (onSuccessToast) onSuccessToast(feedback.message, feedback.tone);
        if (!suppressInlineSuccess) setSuccess(feedback);
        setConfirmStep(false);
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [
    confirmStep,
    action,
    parsedQty,
    parsedPrice,
    parsedStop,
    tif,
    orderType,
    placeUrl,
    buildPayload,
    buildSuccessMessage,
    onSuccessToast,
    suppressInlineSuccess,
    formValid,
    canSubmit,
  ]);

  const quickLabel = (base: string, value: number | null) =>
    showQuickButtonPrices && value != null ? `${base} ${value.toFixed(2)}` : base;

  return (
    <div className={className ? `order-form ${className}` : "order-form"} style={style}>
      {header}

      {priceData != null && (
        <OrderQuoteTelemetry priceData={priceData} label={quoteLabel} density="tight" />
      )}

      <div className="order-field">
        <label className="order-label">Action</label>
        <div className="order-action-buttons">
          <button
            className={`order-action-btn ${action === "BUY" ? "order-action-active order-action-buy" : ""}`}
            onClick={() => selectAction("BUY")}
          >
            BUY
          </button>
          <button
            className={`order-action-btn ${action === "SELL" ? "order-action-active order-action-sell" : ""}`}
            onClick={() => selectAction("SELL")}
          >
            SELL
          </button>
        </div>
      </div>

      <OrderTypeToggle value={orderType} onChange={selectOrderType} />

      <div className="order-field">
        <label className="order-label">Quantity</label>
        <input
          className="order-input"
          type="number"
          min="1"
          step="1"
          value={quantity}
          onChange={(e) => {
            onQuantityChange(e.target.value);
            setConfirmStep(false);
          }}
          placeholder={quantityPlaceholder}
        />
      </div>

      {isStopOrderType(orderType) && (
        <div className="order-field">
          <label className="order-label">Stop Price</label>
          <div className="modify-price-input-row">
            <span className="modify-price-prefix">$</span>
            <input
              className="modify-price-input"
              type="number"
              step="0.01"
              min="0.01"
              value={stopPrice}
              onChange={(e) => {
                setStopPrice(e.target.value);
                setConfirmStep(false);
              }}
              placeholder="0.00"
              data-testid="order-stop-price"
            />
          </div>
          <div className="modify-quick-buttons">
            <button className="btn-quick" disabled={bid == null} onClick={() => setQuickPrice(bid)}>
              {quickLabel("BID", bid)}
            </button>
            <button className="btn-quick" disabled={mid == null} onClick={() => setQuickPrice(mid)}>
              {quickLabel("MID", mid)}
            </button>
            <button className="btn-quick" disabled={ask == null} onClick={() => setQuickPrice(ask)}>
              {quickLabel("ASK", ask)}
            </button>
          </div>
        </div>
      )}

      {orderType !== "STP" && (
        <div className="order-field">
          <label className="order-label">{orderType === "STP LMT" ? "Limit Price" : "Limit Price"}</label>
          <div className="modify-price-input-row">
            <span className="modify-price-prefix">$</span>
            <input
              className="modify-price-input"
              type="number"
              step="0.01"
              min="0.01"
              value={limitPrice}
              onChange={(e) => {
                onLimitPriceChange(e.target.value);
                setConfirmStep(false);
              }}
              placeholder="0.00"
            />
          </div>
          {orderType === "LMT" && (
            <div className="modify-quick-buttons">
              <button className="btn-quick" disabled={bid == null} onClick={() => setQuickPrice(bid)}>
                {quickLabel("BID", bid)}
              </button>
              <button className="btn-quick" disabled={mid == null} onClick={() => setQuickPrice(mid)}>
                {quickLabel("MID", mid)}
              </button>
              <button className="btn-quick" disabled={ask == null} onClick={() => setQuickPrice(ask)}>
                {quickLabel("ASK", ask)}
              </button>
            </div>
          )}
        </div>
      )}

      <div className="order-field">
        <label className="order-label">Time in Force</label>
        <div className="order-action-buttons">
          <button
            className={`order-action-btn ${tif === "DAY" ? "order-action-active" : ""}`}
            onClick={() => selectTif("DAY")}
          >
            DAY
          </button>
          <button
            className={`order-action-btn ${tif === "GTC" ? "order-action-active" : ""}`}
            onClick={() => selectTif("GTC")}
          >
            GTC
          </button>
        </div>
      </div>

      <OrderErrorBanner error={error} />
      {success && (
        <div className={`order-success${success.deduplicated ? " order-success--dedup" : ""}`}>
          {success.message}
        </div>
      )}

      {/* Order Summary (shown in confirm step). Owned by `<OrderRiskGate>`. */}
      {confirmStep && (
        <OrderRiskGate
          input={riskInput}
          portfolio={portfolio}
          surface={riskSurface}
          variant="info"
          paperMode={riskPaperMode}
          onPaperModeChange={onRiskPaperModeChange}
        />
      )}

      <div className="order-submit">
        {confirmStep ? (
          <div className="order-confirm-row">
            <button className="btn-secondary" onClick={() => setConfirmStep(false)} disabled={loading}>
              Back
            </button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!canSubmit || loading}
            >
              {loading ? "Placing..." : "Confirm Order"}
            </button>
          </div>
        ) : (
          <button
            className="btn-primary"
            onClick={handlePlace}
            disabled={!formValid || loading}
            style={{ width: "100%" }}
          >
            Place Order
          </button>
        )}
      </div>
    </div>
  );
}
