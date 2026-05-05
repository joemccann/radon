"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import type { OpenOrder } from "@/lib/types";
import type { OpenOrderDisplayRow } from "@/lib/openOrderCombos";
import { fmtPrice } from "@/lib/positionUtils";
import Card from "./Card";
import BottomSheet from "./BottomSheet";

type HasPermId = { has(permId: number): boolean };

type MobileOrderListProps = {
  rows: OpenOrderDisplayRow[];
  pendingCancelPermIds?: HasPermId;
  pendingModifyPermIds?: HasPermId;
  canModify: (order: OpenOrder) => boolean;
  onRequestCancel: (single: OpenOrder | null, combo: OpenOrder[] | null) => void;
  onRequestModify: (single: OpenOrder | null, combo: OpenOrder[] | null) => void;
};

type ActionTarget = {
  title: string;
  ordersToModify: OpenOrder[];
  ordersToCancel: OpenOrder[];
  modifyEnabled: boolean;
  isPendingCancel: boolean;
  isPendingModify: boolean;
};

function rowSummary(row: OpenOrderDisplayRow): { title: string; subtitle: string; price: string } {
  if (row.kind === "combo") {
    return {
      title: `${row.symbol} · ${row.structure}`,
      subtitle: `${row.totalQuantity}x ${row.summary}`,
      price: row.limitPrice != null ? fmtPrice(row.limitPrice) : "MKT",
    };
  }
  const o = row.order;
  return {
    title: `${o.contract.symbol} · ${o.action}`,
    subtitle: `${o.totalQuantity}x ${o.orderType}${o.tif ? ` · ${o.tif}` : ""}`,
    price: o.limitPrice != null ? fmtPrice(o.limitPrice) : o.orderType === "MKT" ? "MKT" : "—",
  };
}

function pendingFor(row: OpenOrderDisplayRow, cancels: HasPermId, modifies: HasPermId) {
  const orders = row.kind === "combo" ? row.orders : [row.order];
  return {
    cancel: orders.some((o) => cancels.has(o.permId)),
    modify: orders.some((o) => modifies.has(o.permId)),
  };
}

export default function MobileOrderList({
  rows,
  pendingCancelPermIds,
  pendingModifyPermIds,
  canModify,
  onRequestCancel,
  onRequestModify,
}: MobileOrderListProps) {
  const [activeRow, setActiveRow] = useState<OpenOrderDisplayRow | null>(null);

  const cancels: HasPermId = pendingCancelPermIds ?? new Set<number>();
  const modifies: HasPermId = pendingModifyPermIds ?? new Set<number>();

  if (rows.length === 0) {
    return (
      <div className="mobile-empty-state" data-testid="mobile-order-list-empty">
        <span>No open orders.</span>
      </div>
    );
  }

  const closeSheet = () => setActiveRow(null);

  let target: ActionTarget | null = null;
  if (activeRow) {
    if (activeRow.kind === "combo") {
      const pending = pendingFor(activeRow, cancels, modifies);
      target = {
        title: `${activeRow.symbol} · ${activeRow.structure}`,
        ordersToCancel: activeRow.orders,
        ordersToModify: activeRow.orders,
        modifyEnabled: activeRow.orders.every(canModify),
        isPendingCancel: pending.cancel,
        isPendingModify: pending.modify,
      };
    } else {
      const pending = pendingFor(activeRow, cancels, modifies);
      target = {
        title: `${activeRow.order.contract.symbol} · ${activeRow.order.action}`,
        ordersToCancel: [activeRow.order],
        ordersToModify: [activeRow.order],
        modifyEnabled: canModify(activeRow.order),
        isPendingCancel: pending.cancel,
        isPendingModify: pending.modify,
      };
    }
  }

  return (
    <>
      <div className="mobile-card-list" data-testid="mobile-order-list">
        {rows.map((row) => {
          const summary = rowSummary(row);
          const pending = pendingFor(row, cancels, modifies);
          const tone = row.kind === "single" && row.order.action === "SELL" ? "warning" : "default";
          const id = row.kind === "combo" ? row.id : `single-${row.order.permId}`;

          return (
            <Card
              key={id}
              tone={tone}
              testId={`mobile-order-${id}`}
              onClick={() => setActiveRow(row)}
              ariaLabel={summary.title}
            >
              <div className="mobile-card__title-row">
                <div className="mobile-card__title">
                  <span>{summary.title}</span>
                  {(pending.cancel || pending.modify) ? <Loader2 size={12} className="cancel-spinner" /> : null}
                </div>
                <div className="mobile-card__pnl">
                  <div className="mobile-card__pnl-value">{summary.price}</div>
                </div>
              </div>
              <div className="mobile-card__chevron-row">
                <span className="mobile-card__subtitle">{summary.subtitle}</span>
                <span className="mobile-card__subtitle">
                  {pending.cancel ? "Cancelling…" : pending.modify ? "Modifying…" : (row.kind === "combo" ? row.status : row.order.status)}
                </span>
              </div>
            </Card>
          );
        })}
      </div>

      {activeRow && target ? (
        <BottomSheet
          open
          onClose={closeSheet}
          title={target.title}
          testId="mobile-order-action-sheet"
        >
          <div className="mobile-action-sheet">
            <button
              type="button"
              className="mobile-action-sheet__item mobile-action-sheet__item--modify"
              disabled={!target.modifyEnabled || target.isPendingModify}
              onClick={() => {
                if (!activeRow) return;
                if (activeRow.kind === "combo") {
                  onRequestModify(null, target!.ordersToModify);
                } else {
                  onRequestModify(activeRow.order, null);
                }
                closeSheet();
              }}
              data-testid="mobile-order-action-modify"
            >
              Modify limit price
            </button>
            <button
              type="button"
              className="mobile-action-sheet__item mobile-action-sheet__item--cancel"
              disabled={target.isPendingCancel}
              onClick={() => {
                if (!activeRow) return;
                if (activeRow.kind === "combo") {
                  onRequestCancel(null, target!.ordersToCancel);
                } else {
                  onRequestCancel(activeRow.order, null);
                }
                closeSheet();
              }}
              data-testid="mobile-order-action-cancel"
            >
              Cancel order
            </button>
          </div>
        </BottomSheet>
      ) : null}
    </>
  );
}
