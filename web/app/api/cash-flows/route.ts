import { requireRouteAccess } from "@/lib/routeAccess";

import { NextRequest, NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { buildDemoCashFlows } from "@/lib/demo/fixtures/cashFlows";

// Disable Next.js static caching: cash flows update once per day but the
// Turso query is cheap. Without this, the framework freezes the first
// response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "read";

export async function GET(req: NextRequest) {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  const url = new URL(req.url);
  const days = url.searchParams.get("days") ?? "90";
  const types = url.searchParams.get("types") ?? "";
  const requestId = getRequestId();

  if (access.principal.kind === "demo") {
    const parsedDays = Number.parseInt(days, 10);
    const data = buildDemoCashFlows({
      now: new Date(),
      days: Number.isFinite(parsedDays) ? parsedDays : 90,
      types,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  }

  try {
    const data = await radonFetch(`/cash-flows?days=${encodeURIComponent(days)}&types=${encodeURIComponent(types)}`);
    const res = NextResponse.json(data);
    setNoStoreResponseHeaders(res, requestId);
    return res;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    const res = NextResponse.json(
      { rows: [], count: 0, summary: null, error: message },
      { status: 502 },
    );
    setNoStoreResponseHeaders(res, requestId);
    return res;
  }
}
