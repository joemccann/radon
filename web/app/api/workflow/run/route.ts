import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { getRequestId, jsonApiError, scrubSecrets, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { validateWorkflowGraph } from "@/lib/workflow/validateGraph";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// F14 — run a workflow graph server-side. AUTH-REQUIRED. Delegates execution to
// FastAPI (`POST /workflow/run`) where the Python executor lives. Order-emitting
// nodes block unless `confirm_order` is passed — the OrderRiskGate confirmation.

export async function POST(req: Request): Promise<Response> {
  const access = await requireRouteAccess(req, {
    operatorOnly: true,
    rate: { key: "workflow/run:route", limit: 10, windowMs: 60_000 },
    durableRateTier: "B",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();

  let body: { graph?: unknown; confirm_order?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: "Invalid JSON body", requestId }),
      requestId,
    );
  }

  const validation = validateWorkflowGraph(body.graph);
  if (!validation.ok) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: validation.message, requestId }),
      requestId,
    );
  }

  try {
    const report = await radonFetch("/workflow/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ graph: body.graph, confirm_order: body.confirm_order === true }),
      timeout: 120_000,
      token: access.principal.token,
    });
    return setNoStoreResponseHeaders(NextResponse.json(report), requestId);
  } catch (err) {
    const status = err instanceof RadonApiError ? err.status : 502;
    const message = scrubSecrets(err instanceof Error ? err.message : String(err));
    return setNoStoreResponseHeaders(
      jsonApiError({ status, code: "UPSTREAM_ERROR", message: "Workflow run failed", detail: message, requestId }),
      requestId,
    );
  }
}
