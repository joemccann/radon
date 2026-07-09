"use client";

import type { OpenOrder } from "@/lib/types";
import Modal from "./Modal";
import { fmtPrice } from "@/lib/positionUtils";
import { formatFillQuantity } from "@/lib/orders/orderDisplay";

type CancelOrderDialogProps = {
  order?: OpenOrder | null;
  /** Combo multi-leg cancel. When non-empty, takes precedence over `order`. */
  orders?: OpenOrder[] | null;
  loading: boolean;
  onConfirm: () => void;
  onClose: () => void;
};

export default function CancelOrderDialog({
  order = null,
  orders = null,
  loading,
  onConfirm,
  onClose,
}: CancelOrderDialogProps) {
  const multi = orders && orders.length > 0 ? orders : null;
  const single = multi ? null : order;
  if (!multi && !single) return null;

  if (multi) {
    const symbols = Array.from(
      new Set(
        multi.map((o) => o.symbol || o.contract.symbol).filter(Boolean),
      ),
    );
    const symbolLabel =
      symbols.length === 1
        ? symbols[0]
        : symbols.length > 1
          ? `${symbols.length} symbols`
          : "Orders";
    const anyPartial = multi.some((o) => o.filled > 0 && o.remaining > 0);
    return (
      <Modal open onClose={onClose} title="Cancel Orders">
        <div className="cancel-dialog">
          <div className="cancel-order-details">
            <div className="cancel-detail-row">
              <span className="cancel-label">Symbol</span>
              <span className="cancel-value"><strong>{symbolLabel}</strong></span>
            </div>
            <div className="cancel-detail-row">
              <span className="cancel-label">Orders</span>
              <span className="cancel-value">{multi.length}</span>
            </div>
            {multi.map((leg) => (
              <div key={`${leg.permId}-${leg.orderId}`} className="cancel-detail-row">
                <span className="cancel-label">{leg.action}</span>
                <span className="cancel-value">
                  {leg.orderType} · qty {leg.totalQuantity}
                  {leg.limitPrice != null ? ` · ${fmtPrice(leg.limitPrice)}` : ""}
                  {leg.filled > 0 && leg.remaining > 0
                    ? ` · filled ${leg.filled}/${leg.totalQuantity}`
                    : ""}
                </span>
              </div>
            ))}
          </div>

          {anyPartial && (
            <div className="cancel-warning">
              One or more legs are partially filled. Only remaining quantity on each leg will be cancelled.
            </div>
          )}

          <div className="cancel-actions">
            <button className="btn-secondary" onClick={onClose} disabled={loading}>
              Keep Orders
            </button>
            <button className="btn-danger" onClick={onConfirm} disabled={loading}>
              {loading ? "Cancelling..." : "Cancel All"}
            </button>
          </div>
        </div>
      </Modal>
    );
  }

  const target = single!;
  const partiallyFilled = target.filled > 0 && target.remaining > 0;

  return (
    <Modal open={!!target} onClose={onClose} title="Cancel Order">
      <div className="cancel-dialog">
        <div className="cancel-order-details">
          <div className="cancel-detail-row">
            <span className="cancel-label">Symbol</span>
            <span className="cancel-value"><strong>{target.symbol}</strong></span>
          </div>
          <div className="cancel-detail-row">
            <span className="cancel-label">Action</span>
            <span className="cancel-value">
              <span className={`pill ${target.action === "BUY" ? "accum" : "distrib"}`}>
                {target.action}
              </span>
            </span>
          </div>
          <div className="cancel-detail-row">
            <span className="cancel-label">Type</span>
            <span className="cancel-value">{target.orderType}</span>
          </div>
          <div className="cancel-detail-row">
            <span className="cancel-label">Quantity</span>
            <span className="cancel-value">{formatFillQuantity(target)}</span>
          </div>
          {target.limitPrice != null && (
            <div className="cancel-detail-row">
              <span className="cancel-label">Limit Price</span>
              <span className="cancel-value">{fmtPrice(target.limitPrice)}</span>
            </div>
          )}
          <div className="cancel-detail-row">
            <span className="cancel-label">Status</span>
            <span className="cancel-value">{target.status}</span>
          </div>
        </div>

        {partiallyFilled && (
          <div className="cancel-warning">
            Partially filled ({target.filled} of {target.totalQuantity}). Only the remaining {target.remaining} will be cancelled.
          </div>
        )}

        <div className="cancel-actions">
          <button className="btn-secondary" onClick={onClose} disabled={loading}>
            Keep Order
          </button>
          <button className="btn-danger" onClick={onConfirm} disabled={loading}>
            {loading ? "Cancelling..." : "Cancel Order"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
