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

const ALLOWED_ACTIONS = new Set(["start", "stop", "restart"]);
const UNIT_PATTERN = /^radon-[a-z0-9-]+(?:\.service|\.timer)?$|^radon-ib-gateway\.service$/;

export const radonCapability = "admin";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ unit: string; action: string }> },
): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const { unit, action } = await params;

  if (!UNIT_PATTERN.test(unit)) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: `unit ${unit} is not allowed`,
        status: 400,
        code: "BAD_REQUEST",
        requestId,
      }),
      requestId,
    );
  }
  if (!ALLOWED_ACTIONS.has(action)) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: `action ${action} is not allowed`,
        status: 400,
        code: "BAD_REQUEST",
        requestId,
      }),
      requestId,
    );
  }

  try {
    const data = await radonFetch(`/admin/services/${unit}/${action}`, {
      method: "POST",
      // REL-171: must outlive FastAPI REMOTE_TIMEOUT_S (135s) on the app role.
      timeout: 150_000,
      token: access.principal.token,
    });
    const response = NextResponse.json(data);
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "service control failed";
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
