import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { RadonApiError, radonFetch } from "@/lib/radonApi";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

/**
 * POST /api/paper/place — FU6 (B) shadow-placement proxy.
 *
 * Forwards a paper order to the FastAPI ``/paper/place`` route, which runs the
 * shadow matcher + fills engine and persists a simulated fill into
 * ``paper_fills`` (never the live IB journal). Paper-mode orders from
 * ``OrderRiskGate`` route here instead of ``/api/orders/place``.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "mutate.trading";

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "paper/place:route", limit: 20, windowMs: 60_000 }, durableRateTier: "C" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return setNoStoreResponseHeaders(
      jsonApiError({ message: "Invalid JSON body", status: 400, code: "BAD_REQUEST", requestId }),
      requestId,
    );
  }

  try {
    const data = await radonFetch<Record<string, unknown>>("/paper/place", {
      method: "POST",
      body: JSON.stringify(body),
      timeout: 30_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    const message = error instanceof Error ? error.message : "Paper placement failed";
    return setNoStoreResponseHeaders(
      jsonApiError({ message, status, code: "UPSTREAM_ERROR", requestId }),
      requestId,
    );
  }
}
