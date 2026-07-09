/**
 * Pure UX helpers for the /orders control surface:
 * keyboard shortcuts, bulk selection, historical page size.
 */

import type { OpenOrder } from "@/lib/types";
import type { OpenOrderDisplayRow } from "@/lib/openOrderCombos";

export const HISTORICAL_PAGE_SIZES = [15, 30, 50] as const;
export type HistoricalPageSize = (typeof HISTORICAL_PAGE_SIZES)[number];
export const HISTORICAL_PAGE_SIZE_KEY = "radon:orders-historical-page-size";
export const OPEN_ORDERS_DENSITY_KEY = "radon:orders-open-density";
export const DEFAULT_HISTORICAL_PAGE_SIZE: HistoricalPageSize = 15;

export type OpenOrdersDensity = "comfortable" | "compact";

export type OrdersShortcutAction =
  | { type: "focus-filter" }
  | { type: "modify" }
  | { type: "cancel" };

/** Stable key for open-order display rows (single or combo). */
export function openOrderRowKey(row: OpenOrderDisplayRow): string {
  if (row.kind === "combo") return row.id;
  return `${row.order.orderId}-${row.order.permId}`;
}

/** Flatten a display row to the underlying OpenOrder(s). */
export function ordersFromDisplayRow(row: OpenOrderDisplayRow): OpenOrder[] {
  return row.kind === "combo" ? row.orders : [row.order];
}

export function canModifyDisplayRow(
  row: OpenOrderDisplayRow,
  canModify: (order: OpenOrder) => boolean,
): boolean {
  if (row.kind === "combo") return row.orders.every(canModify);
  return canModify(row.order);
}

/** True when keyboard shortcuts must not fire (user is typing). */
export function isEditableKeyboardTarget(el: EventTarget | null): boolean {
  if (!el || !(el instanceof Element)) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return (el as HTMLElement).isContentEditable === true;
}

/**
 * Resolve a desktop open-orders shortcut.
 * Keys: `/` focus filter; `m`/`M` modify; `x`/`X` cancel.
 * Ignores when modifiers are held or when typing targets are focused
 * (caller should pass isTyping=true in those cases).
 */
export function resolveOrdersShortcut(params: {
  key: string;
  isTyping: boolean;
  hasModifier: boolean;
  selectedRowKey: string | null;
  canModifySelected: boolean;
}): OrdersShortcutAction | null {
  const { key, isTyping, hasModifier, selectedRowKey, canModifySelected } = params;
  if (isTyping || hasModifier) return null;

  if (key === "/") return { type: "focus-filter" };

  if (!selectedRowKey) return null;

  const lower = key.length === 1 ? key.toLowerCase() : key;
  if (lower === "m") {
    if (!canModifySelected) return null;
    return { type: "modify" };
  }
  if (lower === "x") {
    return { type: "cancel" };
  }
  return null;
}

/** Collect OpenOrders for all selected display rows (combos flattened). */
export function flattenSelectedOpenOrders(
  rows: readonly OpenOrderDisplayRow[],
  selectedKeys: ReadonlySet<string>,
): OpenOrder[] {
  const out: OpenOrder[] = [];
  const seen = new Set<number>();
  for (const row of rows) {
    const key = openOrderRowKey(row);
    if (!selectedKeys.has(key)) continue;
    for (const order of ordersFromDisplayRow(row)) {
      if (seen.has(order.permId)) continue;
      seen.add(order.permId);
      out.push(order);
    }
  }
  return out;
}

export function toggleSelectionKey(
  selected: ReadonlySet<string>,
  key: string,
  nextChecked: boolean,
): Set<string> {
  const out = new Set(selected);
  if (nextChecked) out.add(key);
  else out.delete(key);
  return out;
}

export function setAllSelectionKeys(
  keys: readonly string[],
  selectAll: boolean,
): Set<string> {
  return selectAll ? new Set(keys) : new Set();
}

export function parseHistoricalPageSize(raw: string | null | undefined): HistoricalPageSize {
  const n = Number(raw);
  if (n === 15 || n === 30 || n === 50) return n;
  return DEFAULT_HISTORICAL_PAGE_SIZE;
}

export function parseOpenOrdersDensity(raw: string | null | undefined): OpenOrdersDensity {
  return raw === "compact" ? "compact" : "comfortable";
}

/**
 * Human range label for pagination.
 * Uses ASCII hyphen (not em dash). Empty total → "Showing 0 of 0".
 */
export function formatShowingRange(
  pageIndex: number,
  pageSize: number,
  total: number,
): string {
  if (total <= 0) return "Showing 0 of 0";
  const start = pageIndex * pageSize + 1;
  const end = Math.min(total, (pageIndex + 1) * pageSize);
  if (start > total) {
    const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1);
    return formatShowingRange(lastPage, pageSize, total);
  }
  return `Showing ${start}-${end} of ${total}`;
}
