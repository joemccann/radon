import { NextResponse } from "next/server";
import { radonFetch, RadonApiError, radonErrorDetailText } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { requireRouteAccess } from "@/lib/routeAccess";

/**
 * Operator credentials proxy in front of FastAPI `/credentials`.
 *
 * The whole surface is operator-only, reads included: the payload carries
 * masked hints plus who-configured-what, which is nobody's business on a
 * demo account. Values never transit this route in either direction on GET.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const UPSTREAM_TIMEOUT_MS = 15_000;

export const radonCapability = { GET: "admin" };

function upstreamFailure(error: unknown, requestId: string): NextResponse {
  const status = error instanceof RadonApiError ? error.status : 502;
  const detail =
    error instanceof RadonApiError
      ? radonErrorDetailText(error.detail)
      : error instanceof Error
        ? error.message
        : "credentials request failed";
  return setNoStoreResponseHeaders(
    jsonApiError({ message: detail, status, code: "UPSTREAM_ERROR", requestId }),
    requestId,
  );
}

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/credentials", {
      method: "GET",
      timeout: UPSTREAM_TIMEOUT_MS,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    return upstreamFailure(error, requestId);
  }
}
