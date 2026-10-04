import { NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { requireRouteAccess } from "@/lib/routeAccess";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "admin";

// IBKR operator hold. Hold: the broker stops the Gateway and keeps it logged
// out so the operator can use IBKR Mobile / the web portal with the shared
// username. Clear: the broker logs the Gateway in once (one 2FA push).
// REL-171: must outlive FastAPI REMOTE_TIMEOUT_S (135s) on the app role.
const TIMEOUT_MS = 150_000;
const REASON_MAX = 200;

function upstreamError(error: unknown, requestId: string, fallback: string): Response {
  const status = error instanceof RadonApiError ? error.status : 502;
  const detail = error instanceof Error ? error.message : fallback;
  return setNoStoreResponseHeaders(
    jsonApiError({ message: detail, status, code: "UPSTREAM_ERROR", requestId }),
    requestId,
  );
}

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/ib/operator-hold", {
      timeout: 15_000,
      token: access.principal.token,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamError(error, requestId, "operator hold status failed");
  }
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    body = null;
  }
  const held = (body as { held?: unknown } | null)?.held;
  const reason = String((body as { reason?: unknown } | null)?.reason ?? "").trim().slice(0, REASON_MAX);
  if (typeof held !== "boolean" || (held && !reason)) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "held must be true or false; a reason is required to set the hold",
        status: 400,
        code: "BAD_REQUEST",
        requestId,
      }),
      requestId,
    );
  }

  try {
    const data = await radonFetch("/ib/operator-hold", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(held ? { held, reason } : { held }),
      timeout: TIMEOUT_MS,
      token: access.principal.token,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamError(error, requestId, "operator hold change failed");
  }
}
