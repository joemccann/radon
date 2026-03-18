import { NextResponse } from "next/server";
import { readDataFile } from "@tools/data-reader";
import { OrdersData } from "@tools/schemas/ib-orders";
import { radonFetch } from "@/lib/radonApi";
import type { ReplaceComboOrder } from "@/lib/orderModify";

export const runtime = "nodejs";

type ModifyBody = {
  orderId?: number;
  permId?: number;
  newPrice?: number;
  newQuantity?: number;
  outsideRth?: boolean;
  replaceOrder?: ReplaceComboOrder;
};

function findOpenOrder(
  orders: OrdersData,
  orderId: number,
  permId: number,
) {
  return orders.open_orders.find((order) =>
    (permId > 0 && order.permId === permId)
    || (orderId > 0 && order.orderId === orderId),
  );
}

function isModifyConfirmed(
  orders: OrdersData,
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
        || !replaceOrder.limitPrice
        || !replaceOrder.legs
        || replaceOrder.legs.length < 2
      ) {
        return NextResponse.json(
          { error: "Invalid combo replacement payload" },
          { status: 400 },
        );
      }

      await radonFetch<Record<string, unknown>>("/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId, permId }),
        timeout: 20_000,
      });

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
      const ordersResult = await readDataFile("data/orders.json", OrdersData);

      return NextResponse.json({
        status: "ok",
        message: result.message,
        orderId: result.orderId,
        permId: result.permId,
        orders: ordersResult.ok ? ordersResult.data : null,
      });
    }

    if (newPrice == null && newQuantity == null && body.outsideRth == null) {
      return NextResponse.json(
        { error: "Must provide at least one modify field" },
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
    const ordersResult = await readDataFile("data/orders.json", OrdersData);

    if (!ordersResult.ok) {
      return NextResponse.json(
        { error: "Modify completed but refreshed orders were unavailable" },
        { status: 502 },
      );
    }

    if (!isModifyConfirmed(ordersResult.data, orderId, permId, newPrice, newQuantity)) {
      return NextResponse.json(
        {
          error: "Modify not confirmed by refreshed orders",
          orders: ordersResult.data,
        },
        { status: 502 },
      );
    }

    return NextResponse.json({
      status: "ok",
      message: result.message,
      orders: ordersResult.data,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Modify failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
