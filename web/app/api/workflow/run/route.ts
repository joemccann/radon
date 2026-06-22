import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// F14 — run a workflow graph server-side. AUTH-REQUIRED. Delegates execution to
// FastAPI (`POST /workflow/run`) where the Python executor lives. Order-emitting
// nodes block unless `confirm_order` is passed — the OrderRiskGate confirmation.

function unauthorized(requestId: string): Response {
  return setNoStoreResponseHeaders(
    jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
    requestId,
  );
}

export async function POST(req: Request): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) return unauthorized(requestId);

  let body: { graph?: unknown; confirm_order?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: "Invalid JSON body", requestId }),
      requestId,
    );
  }

  const graph = body.graph as { nodes?: unknown; edges?: unknown } | undefined;
  if (!graph || !Array.isArray(graph.nodes)) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: "graph.nodes[] required", requestId }),
      requestId,
    );
  }

  try {
    const report = await radonFetch("/workflow/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ graph: body.graph, confirm_order: Boolean(body.confirm_order) }),
      timeout: 120_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json(report), requestId);
  } catch (err) {
    const status = err instanceof RadonApiError ? err.status : 502;
    const message = err instanceof Error ? err.message : String(err);
    return setNoStoreResponseHeaders(
      jsonApiError({ status, code: "UPSTREAM_ERROR", message: "Workflow run failed", detail: message, requestId }),
      requestId,
    );
  }
}
