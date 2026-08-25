import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { tickersBodyToRaw, validateTickerList } from "@/lib/scanTickerList";
import { requireRouteAccess } from "@/lib/routeAccess";

/**
 * POST /api/garch-convergence/scan
 *
 * Triggers garch_convergence.py via FastAPI /garch-convergence/scan.
 * Cooldown + lock live on the FastAPI side. Body accepts {preset?, tickers?}
 * where tickers (comma-separated string or array, paired consecutively so
 * the count must be even) wins over preset; the FastAPI default is
 * preset=largecaps.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  // R-180: this SPAWNS garch_convergence.py. It was classified as "read-only
  // market data" in the filesystem-pinned matrix, whose own contract says a
  // deliberate subprocess trigger belongs in a guarded bucket — its
  // leap/scan sibling has carried the same guard since R-079.
  const access = await requireRouteAccess(request, {
    rate: { key: "garch-convergence/scan:route", limit: 20, windowMs: 60_000 },
  });
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
    const parsed = validateTickerList(rawTickers, { requirePairs: true, dedupe: false });
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

  const path = params.toString()
    ? `/garch-convergence/scan?${params.toString()}`
    : "/garch-convergence/scan";

  try {
    const data = await radonFetch<Record<string, unknown>>(path, {
      method: "POST",
      timeout: 3_610_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (err) {
    const status = err instanceof RadonApiError ? err.status : 502;
    const message = err instanceof Error ? err.message : "GARCH scan failed";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status }),
      requestId,
    );
  }
}
