import { NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { requireDemoAdmin } from "@/lib/demo/adminAuth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(): Promise<Response> {
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
  try {
    const data = await radonFetch("/ib/restart", { method: "POST", timeout: 120_000 });
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
