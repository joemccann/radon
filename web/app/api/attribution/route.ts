import { requireRouteAccess } from "@/lib/routeAccess";
import { getRequestId, jsonApiError } from "@/lib/apiContracts";

import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

export const radonCapability = "read";

export async function GET() {
  const access = await requireRouteAccess(undefined, { rate: { key: "attribution:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  try {
    const data = await radonFetch("/attribution", { timeout: 20_000 });
    return NextResponse.json(data);
  } catch {
    return jsonApiError({ message: "Attribution failed", status: 500, requestId: getRequestId() });
  }
}
