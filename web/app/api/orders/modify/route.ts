import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import type { ModifyCancelTarget, ReplaceComboOrder } from "@/lib/orderModify";
import {
  EMPTY_ORDERS,
  readOrdersSnapshotFromDb,
  type OrdersSnapshot,
} from "@/lib/orders/readOrdersFromDb";

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
  try {
    const body = (await request.json()) as ModifyBody;
    const orderId = body.orderId ?? 0;
    const permId = body.permId ?? 0;
    const newPrice = body.newPrice;
    const newQuantity = body.newQuantity;
    const replaceOrder = body.replaceOrder;

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

      for (const cancelTarget of cancelTargets) {
        await radonFetch<Record<string, unknown>>("/orders/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cancelTarget),
          timeout: 20_000,
        });
      }

      const result = await radonFetch<Record<string, unknown>>("/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(replaceOrder),
        timeout: 20_000,
      });

      try {
        await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
      } catch {
        // Non-fatal
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
    try {
      await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
    } catch {
      // Non-fatal
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
    const message = error instanceof Error ? error.message : "Modify failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
