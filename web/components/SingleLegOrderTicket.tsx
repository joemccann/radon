"use client";

import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import OrderErrorBanner from "./OrderErrorBanner";

export type SingleLegOrderAction = "BUY" | "SELL";
export type SingleLegOrderTif = "DAY" | "GTC";

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
  /** When true the quick buttons append the price (e.g. "BID 1.23"). */
  showQuickButtonPrices?: boolean;
  /** Whether the current form state is submittable. */
  isValid: boolean;
  /** Controlled limit-price value mirror — caller reads it to build risk + payload. */
  limitPrice: string;
  onLimitPriceChange: (value: string) => void;
  /**
   * The caller's `<OrderRiskGate>` (or null). Only rendered in the confirm
   * step. Built by the caller from current action/qty/price.
   */
  riskGate: ReactNode;
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
  }) => Record<string, unknown>;
  /** Human-readable success line, also surfaced to the optional toast sink. */
  buildSuccessMessage: (state: {
    action: SingleLegOrderAction;
    quantity: number;
    limitPrice: number;
  }) => string;
  /** Notified of the live action so callers can build the right risk input. */
  onActionChange?: (action: SingleLegOrderAction) => void;
  /** Notified of the live TIF (callers rarely need it). */
  onTifChange?: (tif: SingleLegOrderTif) => void;
  /**
   * Optional toast sink. When provided, a successful placement routes
   * through it; the inline `.order-success` block is still rendered too
   * unless `suppressInlineSuccess` is set.
   */
  onSuccessToast?: (message: string) => void;
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
  showQuickButtonPrices = false,
  isValid,
  limitPrice,
  onLimitPriceChange,
  riskGate,
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
  const [confirmStep, setConfirmStep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

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
      onLimitPriceChange(value.toFixed(2));
      setConfirmStep(false);
    },
    [onLimitPriceChange],
  );

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      setConfirmStep(true);
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const res = await fetch(placeUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          buildPayload({ action, quantity: parsedQty, limitPrice: parsedPrice, tif }),
        ),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || "Order placement failed");
      } else {
        const message = buildSuccessMessage({
          action,
          quantity: parsedQty,
          limitPrice: parsedPrice,
        });
        if (onSuccessToast) onSuccessToast(message);
        if (!suppressInlineSuccess) setSuccess(message);
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
    tif,
    placeUrl,
    buildPayload,
    buildSuccessMessage,
    onSuccessToast,
    suppressInlineSuccess,
  ]);

  const quickLabel = (base: string, value: number | null) =>
    showQuickButtonPrices && value != null ? `${base} ${value.toFixed(2)}` : base;

  return (
    <div className={className ? `order-form ${className}` : "order-form"} style={style}>
      {header}

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

      <div className="order-field">
        <label className="order-label">Limit Price</label>
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
      {success && <div className="order-success">{success}</div>}

      {/* Order Summary (shown in confirm step). Owned by `<OrderRiskGate>`. */}
      {confirmStep && riskGate}

      <div className="order-submit">
        {confirmStep ? (
          <div className="order-confirm-row">
            <button className="btn-secondary" onClick={() => setConfirmStep(false)} disabled={loading}>
              Back
            </button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!isValid || loading}
            >
              {loading ? "Placing..." : "Confirm Order"}
            </button>
          </div>
        ) : (
          <button
            className="btn-primary"
            onClick={handlePlace}
            disabled={!isValid || loading}
            style={{ width: "100%" }}
          >
            Place Order
          </button>
        )}
      </div>
    </div>
  );
}
