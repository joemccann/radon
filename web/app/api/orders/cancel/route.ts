import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { RadonApiError, radonFetch } from "@/lib/radonApi";
import {
  demoOrderRefusal,
  resolveDemoOrderDecision,
} from "@/lib/demo/orderBlockade";
import {
  EMPTY_ORDERS,
  readOrdersSnapshotFromDb,
} from "@/lib/orders/readOrdersFromDb";
import { invalidateOrdersSnapshotCache } from "@/lib/orders/ordersReadCache";
import {
  isWorkingOrderMissingDetail,
  workingOrderMissingMessage,
} from "@/lib/orders/workingOrders";

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
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    demoBlockadeRoute: true,
    rate: { key: "orders/cancel:route", limit: 5, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  let orderId = 0;
  let permId = 0;
  try {
    const body = (await request.json()) as CancelBody;
    orderId = body.orderId ?? 0;
    permId = body.permId ?? 0;

    // Demo blockade (T-018) — above every radonFetch in this file. A demo user
    // has no live order to cancel (paper fills settle immediately, and there is
    // no /paper/cancel), so an active demo cancel is refused rather than
    // forwarded, and an unresolvable cohort is refused rather than guessed.
    const refusal = demoOrderRefusal(await resolveDemoOrderDecision(), "cancel");
    if (refusal) {
      return NextResponse.json(
        { error: refusal.message, code: refusal.code },
        { status: refusal.status },
      );
    }

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
    invalidateOrdersSnapshotCache();
    try {
      await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
    } catch {
      // Non-fatal
    } finally {
      invalidateOrdersSnapshotCache();
    }
    const orders = await readOrdersSnapshotBestEffort();

    return NextResponse.json({
      status: "ok",
      message: result.message,
      orders,
    });
  } catch (error) {
    invalidateOrdersSnapshotCache();
    if (error instanceof RadonApiError) {
      if (isWorkingOrderMissingDetail(error.detail)) {
        try {
          await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
        } catch {
          // Non-fatal
        } finally {
          invalidateOrdersSnapshotCache();
        }
        const orders = await readOrdersSnapshotBestEffort();
        const tif = orders.open_orders.find((order) =>
          (permId > 0 && order.permId === permId)
          || (orderId > 0 && order.orderId === orderId),
        )?.tif;
        return NextResponse.json(
          { error: workingOrderMissingMessage(tif), orders },
          { status: error.status },
        );
      }
      return NextResponse.json({ error: error.detail }, { status: error.status });
    }
    const message = error instanceof Error ? error.message : "Cancel failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
