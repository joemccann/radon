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

/**
 * Journal lines for one radon-* unit, addressed by its service_health writer
 * name (?service=) or its systemd unit (?unit=). Exactly one is forwarded;
 * FastAPI owns unit resolution and validation.
 */
export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const params = new URL(request.url).searchParams;
  const service = params.get("service");
  const unit = params.get("unit");
  if ((service ? 1 : 0) + (unit ? 1 : 0) !== 1) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "Pass exactly one of service or unit.",
        status: 400,
        code: "BAD_REQUEST",
        requestId,
      }),
      requestId,
    );
  }
  const query = service
    ? `service=${encodeURIComponent(service)}`
    : `unit=${encodeURIComponent(unit as string)}`;
  try {
    const data = await radonFetch(`/admin/logs?${query}`, { method: "GET", timeout: 20_000 });
    const response = NextResponse.json(data);
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "service logs failed";
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
