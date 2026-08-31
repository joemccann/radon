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

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/ib/restart", {
      method: "POST",
      timeout: 120_000,
      token: access.principal.token,
    });
    const response = NextResponse.json(data);
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "restart failed";
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: detail,
        status,
        code: "UPSTREAM_ERROR",
        requestId,
      }),
      requestId,
    );
  }
}
