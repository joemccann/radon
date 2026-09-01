import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { emptyStrengthConfirmationPayload, readStrengthConfirmationCache } from "../route";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function cacheMatchesRequest(cached: Record<string, unknown>, ticker: string, preset: string): boolean {
  const requested = Array.isArray(cached.requested_tickers) ? cached.requested_tickers : [];
  if (ticker) return requested.length === 1 && requested[0] === ticker;
  const universe = typeof cached.universe === "string" ? cached.universe : "";
  return universe === `preset:${preset}` || universe === `fallback:${preset}`;
}

export const radonCapability = "read.spawn";

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "scanner/strength/scan:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  let body: Record<string, unknown> = {};
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    // Empty body is valid. FastAPI supplies defaults.
  }

  const params = new URLSearchParams();
  const ticker = typeof body.ticker === "string" ? body.ticker.trim().toUpperCase() : "";
  const preset = typeof body.preset === "string" && body.preset.trim() ? body.preset.trim() : "ndx100";
  if (ticker && !/^[A-Z]{1,6}$/.test(ticker)) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...emptyStrengthConfirmationPayload(), error: "Ticker must be 1-6 letters" }, { status: 400 }),
      requestId,
    );
  }
  if (ticker) {
    params.set("ticker", ticker);
  } else if (typeof body.preset === "string") {
    params.set("preset", body.preset);
  }
  if (!ticker && typeof body.limit === "number" && Number.isFinite(body.limit) && body.limit > 0) {
    params.set("limit", String(Math.trunc(body.limit)));
  }

  const path = params.toString()
    ? `/strength-confirmation/scan?${params.toString()}`
    : "/strength-confirmation/scan";

  try {
    const data = await radonFetch<Record<string, unknown>>(path, {
      method: "POST",
      timeout: 490_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json({ ...data, scan_succeeded: true }), requestId);
  } catch (err) {
    const status = err instanceof RadonApiError ? err.status : 502;
    if (status >= 500) try {
      const cached = await readStrengthConfirmationCache();
      if (cached && cacheMatchesRequest(cached, ticker, preset)) {
        const res = NextResponse.json(
          { ...cached, is_stale: true, scan_succeeded: false },
          { status },
        );
        res.headers.set("X-Sync-Warning", "Radon API unavailable - matching cached strength confirmation attached");
        return setNoStoreResponseHeaders(res, requestId);
      }
    } catch {
      // Preserve the upstream failure below.
    }
    const message = err instanceof Error ? err.message : "Strength confirmation scan failed";
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...emptyStrengthConfirmationPayload(), scan_succeeded: false, error: message }, { status }),
      requestId,
    );
  }
}
