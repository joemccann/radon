import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { tickersBodyToRaw, validateTickerList } from "@/lib/scanTickerList";

/**
 * POST /api/leap/scan
 *
 * Triggers leap_scanner_uw.py via the FastAPI /leap/scan endpoint. Cooldown
 * + lock live on the FastAPI side. Body accepts {preset?, min_gap?, tickers?}
 * where tickers (comma-separated string or array) wins over preset; preset
 * and min_gap have FastAPI defaults (preset=largecaps, min_gap=10.0).
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "read.spawn";

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "leap/scan:route", limit: 20, windowMs: 60_000 }, durableRateTier: "B" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  let body: Record<string, unknown> = {};
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    // empty body is fine — server uses defaults
  }

  const params = new URLSearchParams();
  const rawTickers = tickersBodyToRaw(body.tickers);
  if (rawTickers.trim().length > 0) {
    const parsed = validateTickerList(rawTickers);
    if (!parsed.ok) {
      return setNoStoreResponseHeaders(
        NextResponse.json({ error: parsed.error }, { status: 400 }),
        requestId,
      );
    }
    params.set("tickers", parsed.tickers.join(","));
  } else if (typeof body.preset === "string") {
    params.set("preset", body.preset);
  }
  if (typeof body.min_gap === "number") params.set("min_gap", String(body.min_gap));

  const path = params.toString() ? `/leap/scan?${params.toString()}` : "/leap/scan";

  try {
    const data = await radonFetch<Record<string, unknown>>(path, {
      method: "POST",
      timeout: 3_610_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (err) {
    const status = err instanceof RadonApiError ? err.status : 502;
    const message = err instanceof Error ? err.message : "LEAP scan failed";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status }),
      requestId,
    );
  }
}
