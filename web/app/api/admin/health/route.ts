import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { ADMIN_HEALTH_UPSTREAM_TIMEOUT_MS } from "@/lib/edgeBudget";
import { classifyUpstreamFailure } from "@/lib/upstreamFailure";

// Live health read — the operator panel polls this to render the IB Gateway
// status card. Must opt out of Next.js static caching so each visit gets a
// fresh /health payload from FastAPI.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "admin";

/**
 * radon-api restarting (deploy, self-heal exit, Restart=always) is an expected
 * state, so it is a 200 degraded verdict rather than a 5xx. No `ib_gateway`
 * key: callers cannot read a broker state from a probe that never ran, so
 * parseIbHealth keeps its last good verdict.
 */
function unreachableHealthResponse(requestId: string, error: unknown): Response {
  const reason = classifyUpstreamFailure(error) ?? "unreachable";
  const detail = error instanceof Error ? error.message : "health probe failed";
  return setNoStoreResponseHeaders(
    NextResponse.json({
      status: "unreachable",
      missing: true,
      api_reachable: false,
      reason,
      error: reason === "timeout" ? "radon-api did not answer in time" : "radon-api is unreachable",
      upstream_error: detail,
    }),
    requestId,
  );
}

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/health", { method: "GET", timeout: ADMIN_HEALTH_UPSTREAM_TIMEOUT_MS });
    const response = NextResponse.json(data);
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    if (!(error instanceof RadonApiError)) return unreachableHealthResponse(requestId, error);
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: error.message,
        status: error.status,
        code: "UPSTREAM_ERROR",
        requestId,
      }),
      requestId,
    );
  }
}
