import { etCalendarDateString } from "./executedToday";

export type WorkingOrderLike = {
  tif?: string | null;
  snapshotAt?: string | null;
};

function parseStamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : new Date(ms);
}

/** True when a DAY order's snapshot is from a previous ET calendar day. */
export function isPriorSessionDayOrder(
  order: WorkingOrderLike,
  updatedAt: string | null | undefined,
  now: Date = new Date(),
): boolean {
  if (String(order.tif ?? "").toUpperCase() !== "DAY") return false;
  const stamped = parseStamp(updatedAt);
  if (!stamped) return false;
  return etCalendarDateString(stamped) < etCalendarDateString(now);
}

export function filterWorkingOpenOrders<T extends WorkingOrderLike>(
  orders: readonly T[],
  now: Date = new Date(),
): T[] {
  return orders.filter((order) => !isPriorSessionDayOrder(order, order.snapshotAt, now));
}

export function workingOrderMissingMessage(tif?: string | null): string {
  if (String(tif ?? "").toUpperCase() === "DAY") {
    return "This DAY order is no longer working at IB. DAY orders end at the regular-session close.";
  }
  return "Order is no longer working at IB. It may have filled or been cancelled.";
}

export function isWorkingOrderMissingDetail(detail: unknown): boolean {
  const text = typeof detail === "string"
    ? detail
    : detail == null
      ? ""
      : JSON.stringify(detail);
  return /trade not found/i.test(text) || /no longer working at ib/i.test(text);
}
