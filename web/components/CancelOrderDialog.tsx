"use client";

import type { OpenOrder } from "@/lib/types";
import Modal from "./Modal";
import { fmtPrice } from "@/lib/positionUtils";

type CancelOrderDialogProps = {
  order: OpenOrder | null;
  loading: boolean;
  onConfirm: () => void;
  onClose: () => void;
};

export default function CancelOrderDialog({ order, loading, onConfirm, onClose }: CancelOrderDialogProps) {
  if (!order) return null;

  const partiallyFilled = order.filled > 0 && order.remaining > 0;

  return (
    <Modal open={!!order} onClose={onClose} title="Cancel Order">
      <div className="cancel-dialog">
        <div className="cancel-order-details">
          <div className="cancel-detail-row">
            <span className="cancel-label">Symbol</span>
            <span className="cancel-value"><strong>{order.symbol}</strong></span>
          </div>
          <div className="cancel-detail-row">
            <span className="cancel-label">Action</span>
            <span className="cancel-value">
              <span className={`pill ${order.action === "BUY" ? "accum" : "distrib"}`}>
                {order.action}
              </span>
            </span>
          </div>
          <div className="cancel-detail-row">
            <span className="cancel-label">Type</span>
            <span className="cancel-value">{order.orderType}</span>
          </div>
          <div className="cancel-detail-row">
            <span className="cancel-label">Quantity</span>
            <span className="cancel-value">{order.totalQuantity}</span>
          </div>
          {order.limitPrice != null && (
            <div className="cancel-detail-row">
              <span className="cancel-label">Limit Price</span>
              <span className="cancel-value">{fmtPrice(order.limitPrice)}</span>
            </div>
          )}
          <div className="cancel-detail-row">
            <span className="cancel-label">Status</span>
            <span className="cancel-value">{order.status}</span>
          </div>
        </div>

        {partiallyFilled && (
          <div className="cancel-warning">
            Partially filled ({order.filled} of {order.totalQuantity}). Only the remaining {order.remaining} will be cancelled.
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
