import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { rateLimit, clientIp, SHARE_CARD_LIMIT, SHARE_WINDOW_MS } from "@/lib/rateLimit";
import { withoutAbsoluteReportPaths } from "@/lib/publicShareRoutes";

export const runtime = "nodejs";

export const radonCapability = "internal";

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { rate: { key: "share-generator", limit: 10, windowMs: 60_000 }, durableRateTier: "B" });
  if (!access.ok) return access.response;
  const limit = rateLimit(clientIp(request), { limit: SHARE_CARD_LIMIT, windowMs: SHARE_WINDOW_MS });
  if (!limit.ok) {
    return NextResponse.json(
      { error: "Too Many Requests" },
      { status: 429, headers: { "Retry-After": String(limit.retryAfterSec) } },
    );
  }

  const live = await readLiveReading(request);
  if (live === INVALID_BODY) {
    return NextResponse.json({ error: "Invalid share request" }, { status: 400 });
  }

  try {
    const token = access.principal.token;
    const data = await radonFetch("/regime/share", { method: "POST", token, ...jsonBody(live) });
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

const INVALID_BODY = Symbol("invalid-body");

/** The page's live CRI reading (`{ live }`), validated by FastAPI. Absent = snapshot only. */
async function readLiveReading(request: Request): Promise<unknown | null | typeof INVALID_BODY> {
  const text = await request.text();
  if (!text) return null;
  try {
    const parsed = JSON.parse(text) as { live?: unknown };
    return parsed?.live ?? null;
  } catch {
    return INVALID_BODY;
  }
}

function jsonBody(live: unknown | null) {
  if (live == null) return {};
  return { headers: { "Content-Type": "application/json" }, body: JSON.stringify(live) };
}
