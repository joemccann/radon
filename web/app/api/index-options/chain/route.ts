import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { OPTION_EXPIRY_PATTERN } from "@/lib/requestBounds";

/**
 * GET /api/index-options/chain?symbol=VIX[&expiry=YYYYMMDD]
 *
 * Proxy to FastAPI /index-options/chain. Returns every listed option
 * contract for the index symbol (or filtered to one expiry) with the
 * IB conId so order placement can reference contracts unambiguously.
 *
 * Currently scoped to VIX/SPX/NDX/RUT/XSP — see INDEX_OPTION_ROOTS in
 * scripts/clients/contract_resolver.py.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "index-options/chain:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol");
  const expiry = searchParams.get("expiry") ?? "";

  const normalizedSymbol = symbol?.toUpperCase() ?? "";
  if (!new Set(["VIX", "SPX", "NDX", "RUT", "XSP"]).has(normalizedSymbol)
    || (expiry !== "" && !OPTION_EXPIRY_PATTERN.test(expiry))) {
    return setNoStoreResponseHeaders(
      NextResponse.json(
        { error: "valid symbol and expiry required", code: "BAD_REQUEST" },
        { status: 400 },
      ),
      requestId,
    );
  }

  const params = new URLSearchParams({ symbol: normalizedSymbol });
  if (expiry) params.set("expiry", expiry);

  try {
    const data = await radonFetch<Record<string, unknown>>(
      `/index-options/chain?${params.toString()}`,
      { timeout: 25_000 },
    );
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (err) {
    const status = err instanceof RadonApiError ? err.status : 502;
    const message = err instanceof Error ? err.message : "index-options chain fetch failed";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status }),
      requestId,
    );
  }
}
