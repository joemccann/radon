"use client";

/**
 * OrderPriceStrip — BID/MID/ASK/SPREAD horizontal strip.
 * Optional onSelect makes quote sides tappable limit-fill controls.
 */

import type { OrderPrices } from "../types";

export type OrderQuoteSide = "bid" | "mid" | "ask";

interface OrderPriceStripProps {
  prices: OrderPrices;
  /** Compact mode for tighter spaces */
  compact?: boolean;
  /** Hide spread column */
  hideSpread?: boolean;
  /** Custom class name */
  className?: string;
  /** Highlight which quote is currently the limit source */
  selected?: OrderQuoteSide | null;
  /** When set, BID/MID/ASK become buttons that fill the limit */
  onSelect?: (side: OrderQuoteSide, value: number) => void;
}

function formatPrice(value: number | null): string {
  if (value == null) return "--";
  return `$${value.toFixed(2)}`;
}

function formatPct(value: number | null): string {
  if (value == null) return "";
  return `(${value.toFixed(1)}%)`;
}

export function OrderPriceStrip({
  prices,
  compact = false,
  hideSpread = false,
  className = "",
  selected = null,
  onSelect,
}: OrderPriceStripProps) {
  const baseClass = compact ? "order-price-strip-compact" : "order-price-strip";
  const interactive = typeof onSelect === "function";

  const renderSide = (
    side: OrderQuoteSide,
    label: string,
    value: number | null,
    valueClass: string,
  ) => {
    const isSelected = selected === side;
    const disabled = value == null;
    const body = (
      <>
        <span className="order-price-label">{label}</span>
        <span className={`order-price-value ${valueClass}`}>{formatPrice(value)}</span>
      </>
    );

    if (!interactive) {
      return (
        <div
          key={side}
          className={`order-price-item${isSelected ? " order-price-item--selected" : ""}`}
        >
          {body}
        </div>
      );
    }

    return (
      <button
        key={side}
        type="button"
        className={`order-price-item order-price-item--btn${isSelected ? " order-price-item--selected" : ""}`}
        disabled={disabled}
        onClick={() => {
          if (value != null) onSelect(side, value);
        }}
        data-testid={`order-price-select-${side}`}
      >
        {body}
      </button>
    );
  };

  return (
    <div
      className={`${baseClass}${interactive ? " order-price-strip--interactive" : ""} ${className}`.trim()}
      data-testid="order-price-strip"
    >
      {renderSide("bid", "BID", prices.bid, "order-price-bid")}
      {renderSide("mid", "MID", prices.mid, "")}
      {renderSide("ask", "ASK", prices.ask, "order-price-ask")}
      {!hideSpread && (
        <div className="order-price-item order-price-spread">
          <span className="order-price-label">SPREAD</span>
          <span className="order-price-value">
            {formatPrice(prices.spread)}
            {prices.spreadPct != null && (
              <span className="order-price-pct"> {formatPct(prices.spreadPct)}</span>
            )}
          </span>
        </div>
      )}
    </div>
  );
}
