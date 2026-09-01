import { NextRequest, NextResponse } from "next/server";
import { radonFetch, RadonApiError, radonErrorDetailText } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { requireRouteAccess } from "@/lib/routeAccess";

/**
 * Per-service credential mutations, proxied to FastAPI `/credentials/{id}`.
 *
 * PUT validates against the vendor before storing; MenthorQ/TheMarketEar run
 * a real browser login the backend bounds at 90s, so the proxy budget is
 * 100s. Upstream status is preserved verbatim — a 422 CREDENTIAL_REJECTED
 * must reach the operator as 422 with the verdict, never a blanket 500.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const MUTATE_TIMEOUT_MS = 100_000;
const DELETE_TIMEOUT_MS = 15_000;
const SERVICE_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const FIELD_PATTERN = /^[A-Z][A-Z0-9_]{0,63}$/;

export const radonCapability = "admin";

function upstreamFailure(error: unknown, requestId: string): NextResponse {
  const status = error instanceof RadonApiError ? error.status : 502;
  const detail =
    error instanceof RadonApiError
      ? radonErrorDetailText(error.detail)
      : error instanceof Error
        ? error.message
        : "credentials request failed";
  const body =
    error instanceof RadonApiError && error.detail && typeof error.detail === "object"
      ? { detail: error.detail }
      : null;
  if (body) {
    // Preserve the structured verdict (code/status/message) end to end.
    return setNoStoreResponseHeaders(
      NextResponse.json(body, { status }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    jsonApiError({ message: detail, status, code: "UPSTREAM_ERROR", requestId }),
    requestId,
  );
}

function badRequest(message: string, requestId: string): NextResponse {
  return setNoStoreResponseHeaders(
    jsonApiError({ message, status: 400, code: "BAD_REQUEST", requestId }),
    requestId,
  );
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ service: string }> },
): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "credentials:mutate", limit: 20, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const operatorId = access.principal.userId;
  const { service } = await params;
  if (!SERVICE_PATTERN.test(service)) {
    return badRequest(`service ${service} is not allowed`, requestId);
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return badRequest("Invalid JSON body", requestId);
  }
  const payload = (body ?? {}) as Record<string, unknown>;
  const values = payload.values;
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    return badRequest("values object is required", requestId);
  }

  try {
    const data = await radonFetch(`/credentials/${encodeURIComponent(service)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values, updated_by: operatorId }),
      timeout: MUTATE_TIMEOUT_MS,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamFailure(error, requestId);
  }
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ service: string }> },
): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "credentials:mutate", limit: 20, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const operatorId = access.principal.userId;
  const { service } = await params;
  if (!SERVICE_PATTERN.test(service)) {
    return badRequest(`service ${service} is not allowed`, requestId);
  }
  const name = new URL(request.url).searchParams.get("name") ?? "";
  if (!FIELD_PATTERN.test(name)) {
    return badRequest("name query parameter is required", requestId);
  }

  try {
    const data = await radonFetch(
      `/credentials/${encodeURIComponent(service)}/${encodeURIComponent(name)}?updated_by=${encodeURIComponent(operatorId)}`,
      { method: "DELETE", timeout: DELETE_TIMEOUT_MS },
    );
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamFailure(error, requestId);
  }
}
