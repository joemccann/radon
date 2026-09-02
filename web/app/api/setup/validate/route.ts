import { NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isSetupMode } from "@/lib/setup/setupMode";
import { verifySetupToken } from "@/lib/setup/setupToken";

/**
 * First-run wizard dry-run vendor check. Proxies FastAPI
 * POST /credentials/{service}/validate with the candidate values; nothing is
 * stored. Token-gated, setup mode only (404 otherwise).
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const SERVICE_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
// MenthorQ / TheMarketEar run a real browser login bounded at 90s upstream.
const VALIDATE_TIMEOUT_MS = 100_000;

export const radonCapability = "internal";

export async function POST(request: Request): Promise<Response> {
  const requestId = getRequestId();
  if (!isSetupMode()) {
    return setNoStoreResponseHeaders(
      jsonApiError({ message: "Not found", status: 404, code: "NOT_FOUND", requestId }),
      requestId,
    );
  }
  let body: { token?: unknown; service?: unknown; values?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }
  if (!verifySetupToken(body.token)) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "Setup token mismatch. It is printed in the terminal that launched Radon.",
        status: 401,
        code: "SETUP_TOKEN_INVALID",
        requestId,
      }),
      requestId,
    );
  }
  const service = typeof body.service === "string" ? body.service : "";
  if (!SERVICE_PATTERN.test(service)) {
    return setNoStoreResponseHeaders(
      jsonApiError({ message: "service is required", status: 400, code: "BAD_REQUEST", requestId }),
      requestId,
    );
  }
  const values = body.values && typeof body.values === "object" && !Array.isArray(body.values)
    ? (body.values as Record<string, unknown>)
    : {};
  try {
    const data = await radonFetch(
      `/credentials/${encodeURIComponent(service)}/validate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
        timeout: VALIDATE_TIMEOUT_MS,
      },
    );
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "The backend is not answering yet. Start the stack (npm run dev) and retry.",
        status,
        code: "BACKEND_UNAVAILABLE",
        requestId,
      }),
      requestId,
    );
  }
}
