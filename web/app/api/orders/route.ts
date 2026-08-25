import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { readOrdersSnapshotFromDb } from "@/lib/orders/readOrdersFromDb";
import { invalidateOrdersSnapshotCache } from "@/lib/orders/ordersReadCache";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

let syncInFlight: Promise<void> | null = null;

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await readOrdersSnapshotFromDb();
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch {
    // 503, not 500: a Turso outage is transient — useOrders keeps last-good
    // client state on any !res.ok and retries on its poll cadence.
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Orders temporarily unavailable" }, { status: 503 }),
      requestId,
    );
  }
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    operatorOnly: true,
    rate: { key: "orders-refresh", limit: 4, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    // Coalesce concurrent POSTs
    if (!syncInFlight) {
      syncInFlight = radonFetch("/orders/refresh", { method: "POST", timeout: 35_000 })
        .then(() => {})
        .finally(() => { syncInFlight = null; });
    }
    await syncInFlight;
  } catch {
    invalidateOrdersSnapshotCache();
    let cached;
    try {
      cached = await readOrdersSnapshotFromDb();
    } catch {
      return setNoStoreResponseHeaders(
        NextResponse.json(
          { error: "Order sync failed" },
          { status: 502 },
        ),
        requestId,
      );
    }
    if (cached.last_sync) {
      console.warn("[Orders] Sync failed, serving latest Turso snapshot");
      const res = NextResponse.json(cached);
      res.headers.set("X-Sync-Warning", "IB sync failed - serving latest Turso snapshot");
      return setNoStoreResponseHeaders(res, requestId);
    }
    return setNoStoreResponseHeaders(
      NextResponse.json(
        { error: "Sync failed" },
        { status: 502 },
      ),
      requestId,
    );
  }

  invalidateOrdersSnapshotCache();
  try {
    const data = await readOrdersSnapshotFromDb();
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Orders temporarily unavailable" }, { status: 500 }),
      requestId,
    );
  }
}
