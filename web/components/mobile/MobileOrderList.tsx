"use client";

import { useState } from "react";
import { Inbox, Loader2 } from "lucide-react";
import type { OpenOrder } from "@/lib/types";
import type { OpenOrderDisplayRow } from "@/lib/openOrderCombos";
import { fmtPrice } from "@/lib/positionUtils";
import Card from "./Card";
import BottomSheet from "./BottomSheet";
import SectionEmptyState from "../SectionEmptyState";

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
    price: o.limitPrice != null ? fmtPrice(o.limitPrice) : o.orderType === "MKT" ? "MKT" : "--",
  };
}

function rowAction(row: OpenOrderDisplayRow): "BUY" | "SELL" | null {
  if (row.kind === "single") return row.order.action === "SELL" ? "SELL" : "BUY";
  // For combos derive from the first leg's action
  const first = row.orders[0];
  if (!first) return null;
  return first.action === "SELL" ? "SELL" : "BUY";
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
      <SectionEmptyState
        icon={Inbox}
        headline="No working orders"
        secondary="Place an order from a ticker view to see it here."
        variant="compact"
        testId="mobile-order-list-empty"
      />
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
          const action = rowAction(row);
          const tone = action === "SELL" ? "negative" : "positive";
          const id = row.kind === "combo" ? row.id : `single-${row.order.permId}`;

          return (
            <div key={id} className="m-card-press">
              <Card
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
                    {pending.cancel ? "Cancelling..." : pending.modify ? "Modifying..." : (row.kind === "combo" ? row.status : row.order.status)}
                  </span>
                </div>
              </Card>
            </div>
          );
        })}
      </div>

      {activeRow && target ? (
        <BottomSheet
          open
          onClose={closeSheet}
          title={target.title}
          testId="mobile-order-action-sheet"
          footer={
            <button
              type="button"
              className="mobile-action-sheet__item m-submit--debit"
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
          }
          destructive={
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
          }
        >
          <div />
        </BottomSheet>
      ) : null}
    </>
  );
}
