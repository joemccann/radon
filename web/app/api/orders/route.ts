import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { readOrdersSnapshotFromDb } from "@/lib/orders/readOrdersFromDb";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

let syncInFlight: Promise<void> | null = null;

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const data = await readOrdersSnapshotFromDb();
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to read orders";
    // 503, not 500: a Turso outage is transient — useOrders keeps last-good
    // client state on any !res.ok and retries on its poll cadence.
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 503 }),
      requestId,
    );
  }
}

export async function POST(): Promise<Response> {
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
    let cached;
    try {
      cached = await readOrdersSnapshotFromDb();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Sync failed and Turso orders read failed";
      return setNoStoreResponseHeaders(
        NextResponse.json(
          { error: message },
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

  try {
    const data = await readOrdersSnapshotFromDb();
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to read orders";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 500 }),
      requestId,
    );
  }
}
