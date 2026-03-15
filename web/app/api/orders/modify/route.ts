import { NextResponse } from "next/server";
import { readDataFile } from "@tools/data-reader";
import { OrdersData } from "@tools/schemas/ib-orders";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

type ModifyBody = {
  orderId?: number;
  permId?: number;
  newPrice?: number;
  outsideRth?: boolean;
};

export async function POST(request: Request): Promise<Response> {
  try {
    const body = (await request.json()) as ModifyBody;
    const orderId = body.orderId ?? 0;
    const permId = body.permId ?? 0;
    const newPrice = body.newPrice;

    if (orderId === 0 && permId === 0) {
      return NextResponse.json(
        { error: "Must provide orderId or permId" },
        { status: 400 },
      );
    }

    if (newPrice == null || newPrice <= 0) {
      return NextResponse.json(
        { error: "Must provide newPrice > 0" },
        { status: 400 },
      );
    }

    const result = await radonFetch<Record<string, unknown>>("/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId, permId, newPrice, outsideRth: body.outsideRth }),
      timeout: 20_000,
    });

    // Refresh orders after modify
    try {
      await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
    } catch {
      // Non-fatal
    }
    const ordersResult = await readDataFile("data/orders.json", OrdersData);

    return NextResponse.json({
      status: "ok",
      message: result.message,
      orders: ordersResult.ok ? ordersResult.data : null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Modify failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
