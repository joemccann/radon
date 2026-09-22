"use client";

import RequestError from "@/components/RequestError";

/**
 * OrderQuantityInput — Quantity input with label
 *
 * Usage:
 *   <OrderQuantityInput value={quantity} onChange={setQuantity} />
 *   <OrderQuantityInput value={quantity} onChange={setQuantity} label="Contracts" />
 */

interface OrderQuantityInputProps {
  value: string;
  onChange: (value: string) => void;
  /** Label text */
  label?: string;
  /** Placeholder text */
  placeholder?: string;
  /** Error message */
  error?: string;
  /** Disabled state */
  disabled?: boolean;
  /** Custom class name */
  className?: string;
}

export function OrderQuantityInput({
  value,
  onChange,
  label = "Quantity",
  placeholder = "Contracts",
  error,
  disabled = false,
  className = "",
}: OrderQuantityInputProps) {
  return (
    <div className={`order-field ${className}`.trim()}>
      <label className="order-label">{label}</label>
      <input
        type="number"
        className={`order-input ${error ? "order-input-error" : ""}`}
        min="1"
        step="1"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={Boolean(error)}
      />
      <RequestError error={error} />
    </div>
  );
}
