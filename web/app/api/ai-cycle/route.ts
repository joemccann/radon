import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { RadonApiError, radonFetch } from "@/lib/radonApi";
import { buildDemoAiCycle } from "@/lib/demo/fixtures/aiCycle";
import { requestPrincipalKind } from "@/lib/demo/requestPrincipal";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  // The demo VM has no /ai-cycle producer; demo principals get a request-time sample.
  if (await requestPrincipalKind() === "demo") {
    return setNoStoreResponseHeaders(NextResponse.json(buildDemoAiCycle(new Date())), requestId);
  }
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
