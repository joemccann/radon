"use client";

import {
  type IbOrderType,
  IB_ORDER_TYPES,
  isStopOrderType,
  orderTypeLabel,
} from "@/lib/order/stopOrder";

export default function OrderTypeToggle({
  value,
  onChange,
  allowStopLimit = true,
}: {
  value: IbOrderType;
  onChange: (next: IbOrderType) => void;
  allowStopLimit?: boolean;
}) {
  const types = allowStopLimit
    ? IB_ORDER_TYPES
    : IB_ORDER_TYPES.filter((type) => type !== "STP LMT");
  return (
    <div className="order-field">
      <label className="order-label">Type</label>
      <div className="order-action-buttons">
        {types.map((type) => (
          <button
            key={type}
            type="button"
            className={`order-action-btn ${value === type ? "order-action-active" : ""}`}
            onClick={() => onChange(type)}
            data-testid={`order-type-${type === "STP LMT" ? "stp-lmt" : type.toLowerCase()}`}
          >
            {orderTypeLabel(type)}
          </button>
        ))}
      </div>
      {isStopOrderType(value) && (
        <span className="position-trade-hint">Sell stop sits below last. Buy stop sits above last.</span>
      )}
    </div>
  );
}
