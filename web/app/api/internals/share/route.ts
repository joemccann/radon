import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { rateLimit, clientIp, SHARE_CARD_LIMIT, SHARE_WINDOW_MS } from "@/lib/rateLimit";
import { withoutAbsoluteReportPaths } from "@/lib/publicShareRoutes";

export const runtime = "nodejs";

export const radonCapability = "internal";

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { rate: { key: "share-generator", limit: 10, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const limit = rateLimit(clientIp(request), { limit: SHARE_CARD_LIMIT, windowMs: SHARE_WINDOW_MS });
  if (!limit.ok) {
    return NextResponse.json(
      { error: "Too Many Requests" },
      { status: 429, headers: { "Retry-After": String(limit.retryAfterSec) } },
    );
  }

  try {
    const token = access.principal.token;
    const data = await radonFetch("/internals/share", { method: "POST", token });
    return NextResponse.json(withoutAbsoluteReportPaths(data));
  } catch (err) {
    if (err instanceof RadonApiError) {
      return NextResponse.json({ error: "Share generation failed" }, { status: err.status });
    }
    return NextResponse.json(
      { error: "Share generation failed" },
      { status: 500 },
    );
  }
}
