import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { RadonApiError } from "@/lib/radonApi";
import {
  demoOrderRefusal,
  resolveDemoOrderDecision,
} from "@/lib/demo/orderBlockade";
import type { ModifyCancelTarget, ReplaceComboOrder } from "@/lib/orderModify";
import {
  EMPTY_ORDERS,
  readOrdersSnapshotFromDb,
  type OrdersSnapshot,
} from "@/lib/orders/readOrdersFromDb";
import { invalidateOrdersSnapshotCache } from "@/lib/orders/ordersReadCache";
import {
  isWorkingOrderMissingDetail,
  workingOrderMissingMessage,
} from "@/lib/orders/workingOrders";

export const runtime = "nodejs";

type ModifyBody = {
  orderId?: number;
  permId?: number;
  newPrice?: number;
  newQuantity?: number;
  outsideRth?: boolean;
  cancelOrders?: ModifyCancelTarget[];
  replaceOrder?: ReplaceComboOrder;
};

/** `/orders/replace` cancels every target BEFORE it places the replacement, so
 *  any failure past that point leaves the operator's exits gone at IB and the
 *  replacement's fate unknown. A flat 500 reads as "nothing happened" — the
 *  message that gets a position left unhedged, or the replacement double-placed
 *  on retry. Mirrors INDETERMINATE_PLACEMENT_MESSAGE on the place route. */
const REPLACE_INDETERMINATE_MESSAGE =
  "Replacement status unknown: the working orders were already cancelled at IB and the replacement was not confirmed. Check open orders before retrying.";

function describeCancelledOrders(cancelled: unknown): string {
  if (!Array.isArray(cancelled) || cancelled.length === 0) return "none reported";
  return cancelled
    .map((entry) => {
      const target = (entry ?? {}) as Record<string, unknown>;
      const id = target.orderId ?? target.permId ?? "unknown";
      return `#${id} (${String(target.status ?? "cancelled")})`;
    })
    .join(", ");
}

/** Render a structured REPLACE_PARTIAL / REPLACE_INDETERMINATE detail as the
 *  `error: string` every other order route returns. */
function replaceFailureMessage(detail: Record<string, unknown>): string {
  const indeterminate = detail.code === "REPLACE_INDETERMINATE";
  const headline = indeterminate
    ? REPLACE_INDETERMINATE_MESSAGE
    : `Replacement incomplete during ${String(detail.phase ?? "unknown")}: the cancelled orders are gone and the replacement did not go live.`;
  return [
    headline,
    `Cancelled: ${describeCancelledOrders(detail.cancelled)}.`,
    `replacementOrderRef=${String(detail.replacementOrderRef ?? "unknown")}.`,
    `Upstream: ${typeof detail.upstream === "string" ? detail.upstream : JSON.stringify(detail.upstream ?? null)}`,
  ].join(" ");
}

function errorDetailAsString(detail: unknown): string {
  if (typeof detail === "string") return detail;
  const record = (detail ?? {}) as Record<string, unknown>;
  if (record.code === "REPLACE_PARTIAL" || record.code === "REPLACE_INDETERMINATE") {
    return replaceFailureMessage(record);
  }
  return JSON.stringify(detail);
}

async function readOrdersSnapshotBestEffort() {
  try {
    return await readOrdersSnapshotFromDb();
  } catch {
    return EMPTY_ORDERS;
  }
}

function findOpenOrder(
  orders: OrdersSnapshot,
  orderId: number,
  permId: number,
) {
  return orders.open_orders.find((order) =>
    (permId > 0 && order.permId === permId)
    || (orderId > 0 && order.orderId === orderId),
  );
}

function isModifyConfirmed(
  orders: OrdersSnapshot,
  orderId: number,
  permId: number,
  newPrice?: number,
  newQuantity?: number,
): boolean {
  const order = findOpenOrder(orders, orderId, permId);
  if (!order) return false;

  const priceConfirmed = newPrice == null
    || (order.limitPrice != null && Math.abs(order.limitPrice - newPrice) < 0.001);
  const quantityConfirmed = newQuantity == null
    || order.totalQuantity === newQuantity;

  return priceConfirmed && quantityConfirmed;
}

function normalizeCancelTargets(
  cancelOrders: ModifyCancelTarget[] | undefined,
  fallbackOrderId: number,
  fallbackPermId: number,
): Array<{ orderId?: number; permId?: number }> {
  const rawTargets = cancelOrders?.length
    ? cancelOrders
    : [{ orderId: fallbackOrderId, permId: fallbackPermId }];

  const seen = new Set<string>();
  const targets: Array<{ orderId?: number; permId?: number }> = [];

  for (const target of rawTargets) {
    const orderId = target.orderId ?? 0;
    const permId = target.permId ?? 0;
    if (orderId <= 0 && permId <= 0) continue;

    const key = permId > 0 ? `perm:${permId}` : `order:${orderId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    targets.push({
      ...(orderId > 0 ? { orderId } : {}),
      ...(permId > 0 ? { permId } : {}),
    });
  }

  return targets;
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    demoBlockadeRoute: true,
    rate: { key: "orders/modify:route", limit: 5, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  let orderId = 0;
  let permId = 0;
  let existingTif: string | undefined;
  let replaceAttempted = false;
  try {
    const body = (await request.json()) as ModifyBody;
    orderId = body.orderId ?? 0;
    permId = body.permId ?? 0;
    const newPrice = body.newPrice;
    const newQuantity = body.newQuantity;
    const replaceOrder = body.replaceOrder;

    // Demo blockade (T-018) — FIRST statement that can reach IB, deliberately
    // ABOVE every radonFetch in this file: the replaceOrder branch below runs a
    // REAL /orders/cancel + /orders/place pair, so a gate placed any lower would
    // leave demo users a live order path. There is no /paper/modify (a paper
    // fill settles immediately, so there is nothing to amend), hence 403 rather
    // than a paper redirect. Non-demo users fall straight through.
    const refusal = demoOrderRefusal(await resolveDemoOrderDecision(), "modify");
    if (refusal) {
      return NextResponse.json(
        { error: refusal.message, code: refusal.code },
        { status: refusal.status },
      );
    }

    if (orderId === 0 && permId === 0) {
      return NextResponse.json(
        { error: "Must provide orderId or permId" },
        { status: 400 },
      );
    }

    if (replaceOrder) {
      if (
        replaceOrder.type !== "combo"
        || !replaceOrder.symbol
        || !replaceOrder.action
        || !replaceOrder.quantity
        || replaceOrder.limitPrice == null
        || replaceOrder.limitPrice === 0
        || !Number.isFinite(replaceOrder.limitPrice)
        || !replaceOrder.legs
        || replaceOrder.legs.length < 2
      ) {
        return NextResponse.json(
          { error: "Invalid combo replacement payload" },
          { status: 400 },
        );
      }

      const cancelTargets = normalizeCancelTargets(body.cancelOrders, orderId, permId);
      if (cancelTargets.length === 0) {
        return NextResponse.json(
          { error: "Must provide at least one order to cancel before combo replacement" },
          { status: 400 },
        );
      }

      replaceAttempted = true;
      const result = await radonFetch<Record<string, unknown>>("/orders/replace", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cancelOrders: cancelTargets, replaceOrder }),
        timeout: 180_000,
      });

      invalidateOrdersSnapshotCache();
      try {
        await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
      } catch {
        // Non-fatal
      } finally {
        invalidateOrdersSnapshotCache();
      }
      const orders = await readOrdersSnapshotBestEffort();

      return NextResponse.json({
        status: "ok",
        message: result.message,
        orderId: result.orderId,
        permId: result.permId,
        orders,
      });
    }

    if (newPrice == null && newQuantity == null && body.outsideRth == null) {
      return NextResponse.json(
        { error: "Must provide at least one modify field: newPrice, newQuantity, or outsideRth" },
        { status: 400 },
      );
    }

    if (newPrice != null && newPrice <= 0) {
      return NextResponse.json(
        { error: "Must provide newPrice > 0" },
        { status: 400 },
      );
    }

    if (newQuantity != null && newQuantity <= 0) {
      return NextResponse.json(
        { error: "Must provide newQuantity > 0" },
        { status: 400 },
      );
    }

    const prior = await readOrdersSnapshotBestEffort();
    existingTif = findOpenOrder(prior, orderId, permId)?.tif;

    const result = await radonFetch<Record<string, unknown>>("/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        orderId,
        permId,
        newPrice,
        newQuantity,
        outsideRth: body.outsideRth,
      }),
      timeout: 20_000,
    });

    // Refresh orders after modify
    invalidateOrdersSnapshotCache();
    try {
      await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
    } catch {
      // Non-fatal
    } finally {
      invalidateOrdersSnapshotCache();
    }
    const orders = await readOrdersSnapshotFromDb();

    if (!isModifyConfirmed(orders, orderId, permId, newPrice, newQuantity)) {
      return NextResponse.json(
        {
          error: "Modify not confirmed by refreshed orders",
          orders,
        },
        { status: 502 },
      );
    }

    return NextResponse.json({
      status: "ok",
      message: result.message,
      orders,
    });
  } catch (error) {
    invalidateOrdersSnapshotCache();
    if (error instanceof RadonApiError) {
      if (isWorkingOrderMissingDetail(error.detail)) {
        try {
          await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
        } catch {
          // Non-fatal: still return the missing-order copy
        } finally {
          invalidateOrdersSnapshotCache();
        }
        const orders = await readOrdersSnapshotBestEffort();
        const tif = existingTif ?? findOpenOrder(orders, orderId, permId)?.tif;
        return NextResponse.json(
          { error: workingOrderMissingMessage(tif), orders },
          { status: error.status },
        );
      }
      return NextResponse.json(
        { error: errorDetailAsString(error.detail) },
        { status: error.status },
      );
    }
    if (replaceAttempted) {
      const reason = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      return NextResponse.json(
        { error: `${REPLACE_INDETERMINATE_MESSAGE} (${reason})` },
        { status: 504 },
      );
    }
    return NextResponse.json({ error: "Order modification failed" }, { status: 500 });
  }
}
