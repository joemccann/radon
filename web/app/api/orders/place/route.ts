import { NextResponse } from "next/server";
import { readDataFile } from "@tools/data-reader";
import { OrdersData } from "@tools/schemas/ib-orders";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

type ComboLeg = {
  expiry: string;
  strike: number;
  right: "C" | "P";
  action: "BUY" | "SELL";
  ratio: number;
};

type PlaceBody = {
  type: "stock" | "option" | "combo";
  symbol: string;
  action: "BUY" | "SELL";
  quantity: number;
  limitPrice: number;
  tif?: "DAY" | "GTC";
  expiry?: string;
  strike?: number;
  right?: "C" | "P";
  legs?: ComboLeg[];
};

export async function POST(request: Request): Promise<Response> {
  try {
    const body = (await request.json()) as PlaceBody;

    if (!body.symbol || !body.action || !body.quantity || !body.limitPrice) {
      return NextResponse.json(
        { error: "Required: symbol, action, quantity, limitPrice" },
        { status: 400 },
      );
    }

    if (body.type === "option" && (!body.expiry || !body.strike || !body.right)) {
      return NextResponse.json(
        { error: "Options require: expiry, strike, right" },
        { status: 400 },
      );
    }

    if (body.type === "combo" && (!body.legs || body.legs.length < 2)) {
      return NextResponse.json(
        { error: "Combo orders require 'legs' array with 2+ entries" },
        { status: 400 },
      );
    }

    const orderPayload = {
      type: body.type || "stock",
      symbol: body.symbol.toUpperCase(),
      action: body.action,
      quantity: body.quantity,
      limitPrice: body.limitPrice,
      tif: body.tif || "DAY",
      ...(body.type === "option" ? { expiry: body.expiry, strike: body.strike, right: body.right } : {}),
      ...(body.type === "combo" ? { legs: body.legs } : {}),
    };

    const orderResult = await radonFetch<Record<string, unknown>>("/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(orderPayload),
      timeout: 20_000,
    });

    // IB silent rejection: order was submitted but immediately cancelled/inactive.
    const REJECTED_STATUSES = new Set(["Cancelled", "ApiCancelled", "Inactive", "Unknown"]);
    const initialStatus = orderResult.initialStatus as string | undefined;
    if (initialStatus && REJECTED_STATUSES.has(initialStatus)) {
      const reason = initialStatus === "Unknown"
        ? `no acknowledgement (${initialStatus}) — order may not have reached IB`
        : initialStatus;
      return NextResponse.json(
        { error: `Order rejected by IB: ${reason}`, detail: orderResult },
        { status: 502 },
      );
    }

    // Refresh orders after placement
    try {
      await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
    } catch {
      // Non-fatal — order was placed, refresh failed
    }
    const ordersResult = await readDataFile("data/orders.json", OrdersData);

    return NextResponse.json({
      status: "ok",
      orderId: orderResult.orderId,
      permId: orderResult.permId,
      initialStatus: orderResult.initialStatus,
      message: orderResult.message,
      orders: ordersResult.ok ? ordersResult.data : null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Order placement failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
