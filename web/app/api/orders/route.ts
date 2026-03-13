import { NextResponse } from "next/server";
import { ibOrders } from "@tools/wrappers/ib-orders";
import { readDataFile } from "@tools/data-reader";
import { OrdersData } from "@tools/schemas/ib-orders";
import { createSyncMutex } from "@/lib/syncMutex";
import type { Static } from "@sinclair/typebox";

export const runtime = "nodejs";

const EMPTY_ORDERS: Static<typeof OrdersData> = {
  last_sync: "",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const readOrders = async (): Promise<Static<typeof OrdersData>> => {
  const result = await readDataFile("data/orders.json", OrdersData);
  return result.ok ? result.data : EMPTY_ORDERS;
};

const syncMutex = createSyncMutex(async () => {
  const result = await ibOrders({ sync: true, port: 4001, clientId: 11 });
  return { ok: result.ok, stderr: result.ok ? "" : result.stderr };
});

export async function GET(): Promise<Response> {
  try {
    const data = await readOrders();
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to read orders";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function POST(): Promise<Response> {
  try {
    const result = await syncMutex();

    if (!result.ok) {
      // Sync failed — fall back to cached data file
      const cached = await readOrders();
      if (cached.last_sync) {
        console.warn("[Orders] Sync failed, serving cached data:", result.stderr);
        const res = NextResponse.json(cached);
        res.headers.set("X-Sync-Warning", "IB sync failed - serving cached data");
        return res;
      }
      // No cached data (empty last_sync) — genuine failure
      return NextResponse.json(
        { error: "Sync failed", stderr: result.stderr },
        { status: 502 },
      );
    }

    const data = await readOrders();
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Sync failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
