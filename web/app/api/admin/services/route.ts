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

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  // Operator-only, fail CLOSED — parity with every sibling admin route. The
  // middleware perimeter fails OPEN when ALLOWED_USER_IDS is unset (the demo
  // posture), so the full fleet unit/service-status list must not lean on it
  // alone.
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
    const data = await radonFetch("/admin/services", { method: "GET", timeout: 15_000 });
    const response = NextResponse.json(data);
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "service list failed";
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
