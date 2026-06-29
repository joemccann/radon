import { NextRequest, NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { requireDemoAdmin } from "@/lib/demo/adminAuth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const ALLOWED_ACTIONS = new Set(["start", "stop", "restart"]);
const UNIT_PATTERN = /^radon-[a-z0-9-]+(?:\.service|\.timer)?$|^radon-ib-gateway\.service$/;

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ unit: string; action: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  // Operator-only, fail CLOSED (see middleware isAuthorizedUser fails open).
  if (!(await requireDemoAdmin())) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "Operator authorization required",
        status: 403,
        code: "FORBIDDEN",
        requestId,
      }),
      requestId,
    );
  }
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
      timeout: 60_000,
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
