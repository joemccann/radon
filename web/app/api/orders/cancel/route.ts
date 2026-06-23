import { NextResponse } from "next/server";
import { RadonApiError, radonFetch } from "@/lib/radonApi";
import {
  EMPTY_ORDERS,
  readOrdersSnapshotFromDb,
} from "@/lib/orders/readOrdersFromDb";

export const runtime = "nodejs";

type CancelBody = {
  orderId?: number;
  permId?: number;
};

async function readOrdersSnapshotBestEffort() {
  try {
    return await readOrdersSnapshotFromDb();
  } catch {
    return EMPTY_ORDERS;
  }
}

export async function POST(request: Request): Promise<Response> {
  try {
    const body = (await request.json()) as CancelBody;
    const orderId = body.orderId ?? 0;
    const permId = body.permId ?? 0;

    if (orderId === 0 && permId === 0) {
      return NextResponse.json(
        { error: "Must provide orderId or permId" },
        { status: 400 },
      );
    }

    const result = await radonFetch<Record<string, unknown>>("/orders/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId, permId }),
      timeout: 20_000,
    });

    // Refresh orders after cancel
    try {
      await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
    } catch {
      // Non-fatal
    }
    const orders = await readOrdersSnapshotBestEffort();

    return NextResponse.json({
      status: "ok",
      message: result.message,
      orders,
    });
  } catch (error) {
    if (error instanceof RadonApiError) {
      return NextResponse.json({ error: error.detail }, { status: error.status });
    }
    const message = error instanceof Error ? error.message : "Cancel failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
