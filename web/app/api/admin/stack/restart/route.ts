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

// Short proxy timeout: radon restart on the VPS bounces FastAPI itself, so
// the upstream connection drops mid-call. If the drop happens after ~3s the
// stop sequence has already kicked in and the restart is in flight; we
// surface that to the client as 202 Accepted rather than 502.
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
    // Connection dropped or timed out — this is the EXPECTED path when the
    // restart succeeds because FastAPI cycles itself. Translate to 202 with
    // a "poll /health to verify" body so the UI doesn't render a failure.
    const looksLikeRestartDrop =
      error instanceof Error &&
      (error.name === "AbortError" ||
        error.message.includes("aborted") ||
        error.message.includes("ECONNRESET") ||
        error.message.includes("fetch failed"));

    if (looksLikeRestartDrop) {
      const response = NextResponse.json(
        {
          unit: "radon-stack",
          action: "restart",
          ok: true,
          in_flight: true,
          detail:
            "radon restart in progress. Backend cycled; poll /health to verify recovery.",
          returncode: 0,
        },
        { status: 202 },
      );
      return setNoStoreResponseHeaders(response, requestId);
    }

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
