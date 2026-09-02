import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isSetupMode } from "@/lib/setup/setupMode";
import { setupTokenRejection } from "@/lib/setup/setupToken";

/**
 * First-run wizard status: verifies the console token and returns the
 * credential service registry (masked state only) from FastAPI. POST, not
 * GET, so the token travels in the body and never lands in access logs.
 * Hard 404 outside setup mode — this surface does not exist once auth is up.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "internal";

export async function POST(request: Request): Promise<Response> {
  const requestId = getRequestId();
  if (!isSetupMode()) {
    return setNoStoreResponseHeaders(
      jsonApiError({ message: "Not found", status: 404, code: "NOT_FOUND", requestId }),
      requestId,
    );
  }
  let body: { token?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }
  const rejected = setupTokenRejection(body.token, requestId);
  if (rejected) return rejected;
  try {
    const data = await radonFetch("/credentials", { method: "GET", timeout: 15_000 });
    return setNoStoreResponseHeaders(
      NextResponse.json({ ok: true, backend: true, credentials: data }),
      requestId,
    );
  } catch {
    // FastAPI not up yet — the wizard can still collect values and validate
    // once the backend arrives; report the state instead of failing.
    return setNoStoreResponseHeaders(
      NextResponse.json({ ok: true, backend: false, credentials: null }),
      requestId,
    );
  }
}
