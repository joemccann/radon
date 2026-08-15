import { NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { requireRouteAccess } from "@/lib/routeAccess";

/**
 * Operator preferences proxy in front of FastAPI `/preferences`.
 *
 * GET is readable by any signed-in user (the page is operator-gated in the
 * perimeter, and the payload carries no secrets). PUT and DELETE mutate live
 * safety caps, so they fail CLOSED behind ALLOWED_USER_IDS via
 * requireRouteAccess({ operatorOnly: true }). The demo-trial admin helper
 * is the wrong gate: that allowlist is unset on app.radon.run and
 * default-denies the operator.
 *
 * Upstream status is preserved verbatim: a 422 validation refusal must reach
 * the operator as 422 with the allowed range, never as a blanket 500.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const UPSTREAM_TIMEOUT_MS = 15_000;

function upstreamFailure(error: unknown, requestId: string): NextResponse {
  const status = error instanceof RadonApiError ? error.status : 502;
  const detail =
    error instanceof RadonApiError
      ? error.detail
      : error instanceof Error
        ? error.message
        : "preferences request failed";
  return setNoStoreResponseHeaders(
    jsonApiError({
      message: detail,
      status,
      code: status === 503 ? "DB_UNAVAILABLE" : "UPSTREAM_ERROR",
      requestId,
    }),
    requestId,
  );
}

function validationFailure(
  message: string,
  requestId: string,
  code: "BAD_REQUEST" | "VALIDATION_ERROR" = "VALIDATION_ERROR",
): NextResponse {
  return setNoStoreResponseHeaders(
    jsonApiError({ message, status: 400, code, requestId }),
    requestId,
  );
}

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/preferences", {
      method: "GET",
      timeout: UPSTREAM_TIMEOUT_MS,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamFailure(error, requestId);
  }
}

export async function PUT(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "preferences:mutate", limit: 20, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const operatorId = access.principal.userId;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return validationFailure("Invalid JSON body", requestId, "BAD_REQUEST");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return validationFailure("Invalid JSON body", requestId, "BAD_REQUEST");
  }
  const payload = body as Record<string, unknown>;
  const key = payload.key;
  if (typeof key !== "string" || !key.trim()) {
    return validationFailure("key is required", requestId);
  }
  if (!("value" in payload)) {
    return validationFailure("value is required", requestId);
  }

  try {
    const data = await radonFetch(`/preferences/${encodeURIComponent(key)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: payload.value, updated_by: operatorId }),
      timeout: UPSTREAM_TIMEOUT_MS,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamFailure(error, requestId);
  }
}

export async function DELETE(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "preferences:mutate", limit: 20, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const operatorId = access.principal.userId;

  const key = new URL(request.url).searchParams.get("key");
  if (!key || !key.trim()) {
    return validationFailure("key is required", requestId);
  }

  try {
    const data = await radonFetch(
      `/preferences/${encodeURIComponent(key)}?updated_by=${encodeURIComponent(operatorId)}`,
      { method: "DELETE", timeout: UPSTREAM_TIMEOUT_MS },
    );
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamFailure(error, requestId);
  }
}
