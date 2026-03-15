import { NextResponse } from "next/server";
import { readDataFile } from "@tools/data-reader";
import { OrdersData } from "@tools/schemas/ib-orders";
import { radonFetch } from "@/lib/radonApi";
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

let syncInFlight: Promise<void> | null = null;

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
    // Coalesce concurrent POSTs
    if (!syncInFlight) {
      syncInFlight = radonFetch("/orders/refresh", { method: "POST", timeout: 35_000 })
        .then(() => {})
        .finally(() => { syncInFlight = null; });
    }
    await syncInFlight;

    const data = await readOrders();
    return NextResponse.json(data);
  } catch {
    // Sync failed — fall back to cached data file
    const cached = await readOrders();
    if (cached.last_sync) {
      console.warn("[Orders] Sync failed, serving cached data");
      const res = NextResponse.json(cached);
      res.headers.set("X-Sync-Warning", "IB sync failed - serving cached data");
      return res;
    }
    // No cached data (empty last_sync) — genuine failure
    return NextResponse.json(
      { error: "Sync failed" },
      { status: 502 },
    );
  }
}
