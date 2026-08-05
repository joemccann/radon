"use client";

import { useState } from "react";
import { Inbox, Loader2 } from "lucide-react";
import type { OpenOrder, PortfolioPosition } from "@/lib/types";
import type { OpenOrderDisplayRow } from "@/lib/openOrderCombos";
import { fmtPrice } from "@/lib/positionUtils";
import {
  cardToneForIntent,
  formatFillQuantity,
  mapOrderStatus,
  resolveOrderIntent,
  type OrderIntent,
  type OrderStatusTone,
} from "@/lib/orders/orderDisplay";
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
  /** Optional portfolio for OPEN/CLOSE intent and card tone. */
  portfolioPositions?: readonly PortfolioPosition[];
};

type ActionTarget = {
  title: string;
  ordersToModify: OpenOrder[];
  ordersToCancel: OpenOrder[];
  modifyEnabled: boolean;
  isPendingCancel: boolean;
  isPendingModify: boolean;
  isCombo: boolean;
};

function limitLabel(limitPrice: number | null, orderType: string): string {
  if (limitPrice != null) return fmtPrice(limitPrice);
  if (orderType === "MKT") return "MKT";
  return "--";
}

function comboFillQuantity(orders: readonly OpenOrder[], totalQuantity: number): string {
  if (orders.length === 0) return String(totalQuantity);
  const filled = Math.min(...orders.map((o) => o.filled));
  const remaining = Math.max(...orders.map((o) => o.remaining));
  return formatFillQuantity({ filled, remaining, totalQuantity });
}

function statusInfo(
  raw: string,
  opts: {
    filled?: number;
    remaining?: number;
    isPendingCancel: boolean;
    isPendingModify: boolean;
  },
): { label: string; tone: OrderStatusTone } {
  if (opts.isPendingCancel) return { label: "Cancelling...", tone: "pending" };
  if (opts.isPendingModify) return { label: "Modifying...", tone: "pending" };
  const mapped = mapOrderStatus(raw, {
    filled: opts.filled,
    remaining: opts.remaining,
  });
  return { label: mapped.label, tone: mapped.tone };
}

function statusLabel(
  raw: string,
  opts: {
    filled?: number;
    remaining?: number;
    isPendingCancel: boolean;
    isPendingModify: boolean;
  },
): string {
  return statusInfo(raw, opts).label;
}

/** Chip modifier per status tone; calm states stay untinted. */
const STATUS_CHIP_TONE: Record<OrderStatusTone, string | null> = {
  working: null,
  neutral: null,
  done: null,
  partial: "warn",
  pending: "busy",
  inactive: "muted",
};

function StatusChip({ label, tone }: { label: string; tone: OrderStatusTone }) {
  const modifier = STATUS_CHIP_TONE[tone];
  return (
    <span
      className={`m-order-status${modifier ? ` m-order-status--${modifier}` : ""}`}
      data-testid="mobile-order-status"
    >
      {label}
    </span>
  );
}

function rowSummary(
  row: OpenOrderDisplayRow,
  pending: { cancel: boolean; modify: boolean },
): {
  title: string;
  subtitle: string;
  price: string;
  status: string;
  statusTone: OrderStatusTone;
} {
  if (row.kind === "combo") {
    const qty = comboFillQuantity(row.orders, row.totalQuantity);
    const first = row.orders[0];
    const status = statusInfo(row.status ?? "", {
      filled: first?.filled,
      remaining: first?.remaining,
      isPendingCancel: pending.cancel,
      isPendingModify: pending.modify,
    });
    return {
      title: `${row.symbol} · ${row.structure}`,
      subtitle: `${qty}x ${row.summary}`,
      price: limitLabel(row.limitPrice, row.orderType),
      status: status.label,
      statusTone: status.tone,
    };
  }
  const o = row.order;
  const qty = formatFillQuantity(o);
  const status = statusInfo(o.status ?? "", {
    filled: o.filled,
    remaining: o.remaining,
    isPendingCancel: pending.cancel,
    isPendingModify: pending.modify,
  });
  return {
    title: `${o.contract.symbol} · ${o.action}`,
    subtitle: `${qty}x ${o.orderType}${o.tif ? ` · ${o.tif}` : ""}`,
    price: limitLabel(o.limitPrice, o.orderType),
    status: status.label,
    statusTone: status.tone,
  };
}

function rowAction(row: OpenOrderDisplayRow): "BUY" | "SELL" | null {
  if (row.kind === "single") return row.order.action === "SELL" ? "SELL" : "BUY";
  const first = row.orders[0];
  if (!first) return null;
  return first.action === "SELL" ? "SELL" : "BUY";
}

function rowIntent(
  row: OpenOrderDisplayRow,
  portfolio: readonly PortfolioPosition[] | undefined,
): OrderIntent {
  if (row.kind === "single") {
    return resolveOrderIntent(row.order, portfolio);
  }
  // Combos are BAG-like; try first leg when portfolio coverage is clear.
  const first = row.orders[0];
  if (!first) return "UNKNOWN";
  return resolveOrderIntent(first, portfolio);
}

function rowCardTone(
  intent: OrderIntent,
  action: "BUY" | "SELL" | null,
  isCombo: boolean,
): "positive" | "negative" | "default" {
  if (isCombo && intent === "UNKNOWN") return "default";
  return cardToneForIntent(intent, action ?? "");
}

function pendingFor(row: OpenOrderDisplayRow, cancels: HasPermId, modifies: HasPermId) {
  const orders = row.kind === "combo" ? row.orders : [row.order];
  return {
    cancel: orders.some((o) => cancels.has(o.permId)),
    modify: orders.some((o) => modifies.has(o.permId)),
  };
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="mobile-card__metric">
      <span className="mobile-card__metric-label">{label}</span>
      <span className="mobile-card__metric-value">{value}</span>
    </div>
  );
}

function IntentBadge({ intent }: { intent: OrderIntent }) {
  if (intent === "UNKNOWN") return null;
  const pillClass = intent === "CLOSE" ? "distrib" : "accum";
  return (
    <span className={`pill ${pillClass}`} data-testid="mobile-order-intent-badge">
      {intent}
    </span>
  );
}

function OrderSheetSummary({
  row,
  pending,
  portfolio,
}: {
  row: OpenOrderDisplayRow;
  pending: { cancel: boolean; modify: boolean };
  portfolio: readonly PortfolioPosition[] | undefined;
}) {
  const intent = rowIntent(row, portfolio);

  if (row.kind === "combo") {
    const first = row.orders[0];
    const qty = comboFillQuantity(row.orders, row.totalQuantity);
    const status = statusLabel(row.status ?? "", {
      filled: first?.filled,
      remaining: first?.remaining,
      isPendingCancel: pending.cancel,
      isPendingModify: pending.modify,
    });
    return (
      <div className="mobile-order-sheet-summary" data-testid="mobile-order-sheet-summary">
        <div className="mobile-card__title-row">
          <div className="mobile-card__title">
            <span>{row.symbol}</span>
            <IntentBadge intent={intent} />
          </div>
        </div>
        {row.summary ? <div className="mobile-card__subtitle">{row.summary}</div> : null}
        <div className="mobile-card__metrics">
          <Metric label="Qty" value={qty} />
          <Metric label="Limit" value={limitLabel(row.limitPrice, row.orderType)} />
          <Metric label="Status" value={status} />
          <Metric label="TIF" value={row.tif || "--"} />
        </div>
        <div className="mobile-card__detail" data-testid="mobile-order-sheet-legs">
          {row.orders.map((leg) => {
            const legLabel = leg.contract.secType === "OPT" && leg.contract.strike != null
              ? `${leg.action} ${formatFillQuantity(leg)}x ${leg.contract.right ?? ""} ${leg.contract.strike}`
              : `${leg.action} ${formatFillQuantity(leg)}x ${leg.contract.secType}`;
            return (
              <div key={leg.permId} className="mobile-card__leg-row">
                <div className="mobile-card__leg-desc">{legLabel}</div>
                <div className="mobile-card__leg-metrics">
                  <span className="mobile-card__leg-meta">
                    {limitLabel(leg.limitPrice, leg.orderType)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  const o = row.order;
  const status = statusLabel(o.status ?? "", {
    filled: o.filled,
    remaining: o.remaining,
    isPendingCancel: pending.cancel,
    isPendingModify: pending.modify,
  });
  return (
    <div className="mobile-order-sheet-summary" data-testid="mobile-order-sheet-summary">
      <div className="mobile-card__title-row">
        <div className="mobile-card__title">
          <span>{o.contract.symbol}</span>
          <IntentBadge intent={intent} />
        </div>
      </div>
      <div className="mobile-card__metrics">
        <Metric label="Qty" value={formatFillQuantity(o)} />
        <Metric label="Limit" value={limitLabel(o.limitPrice, o.orderType)} />
        <Metric label="Status" value={status} />
        <Metric label="TIF" value={o.tif || "--"} />
      </div>
    </div>
  );
}

type OrderSortKey = "default" | "symbol" | "totalQuantity" | "limitPrice" | "status";

export const ORDER_SORT_CHIPS: { key: OrderSortKey; label: string }[] = [
  { key: "default", label: "Default" },
  { key: "symbol", label: "Symbol" },
  { key: "totalQuantity", label: "Qty" },
  { key: "limitPrice", label: "Limit" },
  { key: "status", label: "Status" },
];

function rowSortValue(row: OpenOrderDisplayRow, key: OrderSortKey): string | number {
  if (row.kind === "combo") {
    if (key === "symbol") return row.symbol;
    if (key === "totalQuantity") return row.totalQuantity;
    if (key === "limitPrice") return row.limitPrice ?? -Infinity;
    if (key === "status") return row.status ?? "";
  } else {
    const o = row.order;
    if (key === "symbol") return o.contract.symbol;
    if (key === "totalQuantity") return o.totalQuantity;
    if (key === "limitPrice") return o.limitPrice ?? -Infinity;
    if (key === "status") return o.status ?? "";
  }
  return 0;
}

function sortRows(rows: OpenOrderDisplayRow[], key: OrderSortKey): OpenOrderDisplayRow[] {
  if (key === "default") return rows;
  return [...rows].sort((a, b) => {
    const av = rowSortValue(a, key);
    const bv = rowSortValue(b, key);
    if (typeof av === "number" && typeof bv === "number") return bv - av;
    return String(av).localeCompare(String(bv));
  });
}

export default function MobileOrderList({
  rows,
  pendingCancelPermIds,
  pendingModifyPermIds,
  canModify,
  onRequestCancel,
  onRequestModify,
  portfolioPositions,
}: MobileOrderListProps) {
  const [activeRow, setActiveRow] = useState<OpenOrderDisplayRow | null>(null);
  const [sortKey, setSortKey] = useState<OrderSortKey>("default");

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
        isCombo: true,
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
        isCombo: false,
      };
    }
  }

  return (
    <>
      <div className="m-sortbar" role="toolbar" aria-label="Sort orders">
        {ORDER_SORT_CHIPS.map(({ key, label }) => (
          <button
            key={key}
            className={`m-chip${sortKey === key ? " m-chip--active" : ""}`}
            onClick={() => setSortKey(key)}
            type="button"
          >
            {label}
          </button>
        ))}
      </div>
      <div className="mobile-card-list" data-testid="mobile-order-list">
        {sortRows(rows, sortKey).map((row) => {
          const pending = pendingFor(row, cancels, modifies);
          const summary = rowSummary(row, pending);
          const action = rowAction(row);
          const intent = rowIntent(row, portfolioPositions);
          const tone = rowCardTone(intent, action, row.kind === "combo");
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
                    {row.kind === "single" ? (
                      <>
                        <span>{row.order.contract.symbol}</span>
                        <span
                          className={`m-order-side m-order-side--${action === "SELL" ? "sell" : "buy"}`}
                          data-testid="mobile-order-side"
                        >
                          {action}
                        </span>
                      </>
                    ) : (
                      <span>{summary.title}</span>
                    )}
                    {(pending.cancel || pending.modify) ? <Loader2 size={12} className="cancel-spinner" /> : null}
                  </div>
                  <div className="mobile-card__pnl">
                    <div className="mobile-card__pnl-value">{summary.price}</div>
                  </div>
                </div>
                <div className="mobile-card__chevron-row">
                  <span className="mobile-card__subtitle">{summary.subtitle}</span>
                  <StatusChip label={summary.status} tone={summary.statusTone} />
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
              {target.isCombo ? "Cancel all legs" : "Cancel order"}
            </button>
          }
        >
          <OrderSheetSummary
            row={activeRow}
            pending={{ cancel: target.isPendingCancel, modify: target.isPendingModify }}
            portfolio={portfolioPositions}
          />
        </BottomSheet>
      ) : null}
    </>
  );
}
