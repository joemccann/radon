import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { RadonApiError } from "@/lib/radonApi";

// Must stay above Python REQUEST_PATH_AUTH_BUDGET_SECONDS (40s).
export const OPTIONS_PROXY_TIMEOUT_MS = 50_000;

// Single definition lives in the domain lib so the client hook can share it
// without pulling next/server into the browser bundle.
export { RV_RATIO_SCAN_TIMEOUT_MS } from "@/lib/rvRatio";

function isTimeoutError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const name = error.name.toLowerCase();
  const message = error.message.toLowerCase();
  return name.includes("timeout") || message.includes("timeout") || message.includes("timed out");
}

export function optionsJson(payload: Record<string, unknown>, status = 200): NextResponse {
  return setNoStoreResponseHeaders(
    NextResponse.json(payload, { status }),
    getRequestId(),
  );
}

export function optionsErrorResponse(
  label: string,
  error: unknown,
): NextResponse {
  const status =
    error instanceof RadonApiError ? error.status : isTimeoutError(error) ? 504 : 502;
  return optionsJson(
    {
      error: label,
      detail: status === 504 ? "Options request timed out" : "Options service unavailable",
      code: status === 504 ? "UPSTREAM_TIMEOUT" : "UPSTREAM_ERROR",
    },
    status,
  );
}
