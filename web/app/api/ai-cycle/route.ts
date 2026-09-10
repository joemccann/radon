import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { RadonApiError, radonFetch } from "@/lib/radonApi";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/ai-cycle", { timeout: 45_000, cache: "no-store" });
    const response = NextResponse.json(data);
    setNoStoreResponseHeaders(response, requestId);
    return response;
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    const detail = error instanceof RadonApiError
      ? error.detail
      : "AI infrastructure observations are temporarily unavailable";
    const response = NextResponse.json({ error: detail }, { status });
    setNoStoreResponseHeaders(response, requestId);
    return response;
  }
}
