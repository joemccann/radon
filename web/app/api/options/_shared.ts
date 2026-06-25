import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { RadonApiError } from "@/lib/radonApi";

export const OPTIONS_PROXY_TIMEOUT_MS = 50_000;

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
  const detail =
    error instanceof RadonApiError
      ? error.detail
      : error instanceof Error
        ? error.message
        : "Upstream options service failed";

  return optionsJson(
    {
      error: label,
      detail,
      code: status === 504 ? "UPSTREAM_TIMEOUT" : "UPSTREAM_ERROR",
    },
    status,
  );
}
