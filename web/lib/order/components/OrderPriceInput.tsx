"use client";

import RequestError from "@/components/RequestError";

/**
 * OrderPriceInput — Price input with $ prefix and quick-fill buttons
 *
 * Usage:
 *   <OrderPriceInput value={price} onChange={setPrice} prices={orderPrices} />
 */

import type { OrderPrices } from "../types";
import { OrderPriceButtons } from "./OrderPriceButtons";

interface OrderPriceInputProps {
  value: string;
  onChange: (value: string) => void;
  prices: OrderPrices;
  /** Label text */
  label?: string;
  /** Placeholder text */
  placeholder?: string;
  /** Error message */
  error?: string;
  /** Disabled state */
  disabled?: boolean;
  /** Show prices in quick buttons */
  showButtonPrices?: boolean;
  /** Custom class name */
  className?: string;
}

export function OrderPriceInput({
  value,
  onChange,
  prices,
  label = "Limit Price",
  placeholder = "0.00",
  error,
  disabled = false,
  showButtonPrices = true,
  className = "",
}: OrderPriceInputProps) {
  return (
    <div className={`order-field ${className}`.trim()}>
      <label className="order-label">{label}</label>
      <div className="order-price-input-row">
        <span className="order-price-prefix">$</span>
        <input
          type="number"
          className={`order-price-input ${error ? "order-input-error" : ""}`}
          step="0.01"
          min="0.01"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          aria-invalid={Boolean(error)}
        />
      </div>
      <OrderPriceButtons
        prices={prices}
        onSelect={onChange}
        showPrices={showButtonPrices}
        disabled={disabled}
      />
      <RequestError error={error} />
    </div>
  );
}
