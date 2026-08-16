import { NextRequest, NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { requireRouteAccess } from "@/lib/routeAccess";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Operator-reachable kill switch (REL-029 / R-053). Proxies the REL-004
 * FastAPI controls so the browser can fire them through the Next.js
 * perimeter — Caddy mounts FastAPI behind X-Forwarded-For, so a direct
 * browser call can never satisfy its loopback trust.
 *
 * Destructive actions (kill, cancel-all) demand an explicit {confirm:true}
 * body from the client so no stray fetch can mass-cancel.
 */
const POST_ACTIONS: Record<string, { path: string; confirm: boolean }> = {
  halt: { path: "/trading/halt", confirm: false },
  resume: { path: "/trading/resume", confirm: false },
  kill: { path: "/trading/kill", confirm: true },
  "cancel-all": { path: "/orders/cancel-all", confirm: true },
};

function badRequest(message: string, requestId: string): Response {
  return setNoStoreResponseHeaders(
    jsonApiError({ message, status: 400, code: "BAD_REQUEST", requestId }),
    requestId,
  );
}

function upstreamError(error: unknown, requestId: string): Response {
  const status = error instanceof RadonApiError ? error.status : 502;
  const detail = error instanceof Error ? error.message : "trading control failed";
  return setNoStoreResponseHeaders(
    jsonApiError({ message: detail, status, code: "UPSTREAM_ERROR", requestId }),
    requestId,
  );
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ action: string }> },
): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const { action } = await params;
  if (action !== "status") {
    return badRequest(`action ${action} is not allowed`, requestId);
  }
  try {
    const data = await radonFetch("/trading/status", { timeout: 10_000 });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamError(error, requestId);
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ action: string }> },
): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const { action } = await params;

  const target = POST_ACTIONS[action];
  if (!target) {
    return badRequest(`action ${action} is not allowed`, requestId);
  }

  const body = (await req.json().catch(() => ({}))) as Record<string, unknown>;
  if (target.confirm && body.confirm !== true) {
    return badRequest(
      `POST {"confirm": true} to run ${action}`,
      requestId,
    );
  }

  const upstreamBody =
    action === "cancel-all"
      ? { confirm: true }
      : typeof body.reason === "string"
        ? { reason: body.reason }
        : {};

  try {
    const data = await radonFetch(target.path, {
      method: "POST",
      body: JSON.stringify(upstreamBody),
      headers: { "content-type": "application/json" },
      // The kill path runs a bounded 30s mass-cancel drain upstream.
      timeout: 45_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamError(error, requestId);
  }
}
