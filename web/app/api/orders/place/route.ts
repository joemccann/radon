import { NextResponse } from "next/server";
import { runScript } from "@tools/runner";
import { ibOrders } from "@tools/wrappers/ib-orders";
import { readDataFile } from "@tools/data-reader";
import { OrdersData } from "@tools/schemas/ib-orders";

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

    const orderJson = JSON.stringify({
      type: body.type || "stock",
      symbol: body.symbol.toUpperCase(),
      action: body.action,
      quantity: body.quantity,
      limitPrice: body.limitPrice,
      tif: body.tif || "DAY",
      ...(body.type === "option" ? { expiry: body.expiry, strike: body.strike, right: body.right } : {}),
      ...(body.type === "combo" ? { legs: body.legs } : {}),
    });

    const result = await runScript("scripts/ib_place_order.py", {
      args: ["--json", orderJson],
      timeout: 15_000,
    });

    if (!result.ok) {
      return NextResponse.json(
        { error: "Order placement failed", stderr: result.stderr },
        { status: 502 },
      );
    }

    const orderResult = result.data as Record<string, unknown>;

    if (orderResult.status === "error") {
      return NextResponse.json(
        { error: orderResult.message, detail: orderResult },
        { status: 502 },
      );
    }

    // Refresh orders after placement
    await ibOrders({ sync: true, port: 4001, clientId: 11 });
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
