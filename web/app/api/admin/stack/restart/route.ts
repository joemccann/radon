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

// Bound the operator request. A connection loss is not proof that FastAPI
// accepted the restart, so the route must fail closed rather than manufacture
// an in-flight success. The UI keeps polling read-only health afterward.
const PROXY_TIMEOUT_MS = 5_000;

export async function POST(_req: NextRequest): Promise<Response> {
  const requestId = getRequestId();
  // Operator-only, fail CLOSED. Unlike middleware's isAuthorizedUser (which
  // fails open when ALLOWED_USER_IDS is unset), these high-impact ops routes
  // (full stack restart) must DENY when no allowlist is configured.
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
    const data = await radonFetch("/admin/stack/restart", {
      method: "POST",
      timeout: PROXY_TIMEOUT_MS,
    });
    const response = NextResponse.json(data);
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    const detail =
      error instanceof Error ? error.message : "stack restart failed";
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
