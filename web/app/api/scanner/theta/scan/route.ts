import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { emptyThetaHarvesterPayload, readThetaHarvesterCache } from "../route";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function cacheMatchesRequest(cached: Record<string, unknown>, ticker: string, preset: string): boolean {
  const requested = Array.isArray(cached.requested_tickers) ? cached.requested_tickers : [];
  if (ticker) return requested.length === 1 && requested[0] === ticker;
  const universe = typeof cached.universe === "string" ? cached.universe : "";
  return universe === `preset:${preset}` || universe === `fallback:${preset}`;
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "scanner/theta/scan:route", limit: 20, windowMs: 60_000 } });
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
      NextResponse.json({ ...emptyThetaHarvesterPayload(), error: "Ticker must be 1-6 letters" }, { status: 400 }),
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

  // Search parameters (DTE window + minimum per-share credit). FastAPI validates
  // ranges; only forward finite numbers so bad input never reaches the subprocess.
  const intParam = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null;
  const minDte = intParam(body.min_dte);
  const maxDte = intParam(body.max_dte);
  const minCredit =
    typeof body.min_credit === "number" && Number.isFinite(body.min_credit) && body.min_credit >= 0
      ? body.min_credit
      : null;
  if (minDte !== null) params.set("min_dte", String(minDte));
  if (maxDte !== null) params.set("max_dte", String(maxDte));
  if (minCredit !== null) params.set("min_credit", String(minCredit));

  const path = params.toString()
    ? `/theta-harvester/scan?${params.toString()}`
    : "/theta-harvester/scan";

  try {
    const data = await radonFetch<Record<string, unknown>>(path, {
      method: "POST",
      timeout: 430_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json({ ...data, scan_succeeded: true }), requestId);
  } catch (err) {
    const status = err instanceof RadonApiError ? err.status : 502;
    if (status >= 500) try {
      const cached = await readThetaHarvesterCache();
      if (cached && cacheMatchesRequest(cached, ticker, preset)) {
        const res = NextResponse.json(
          { ...cached, is_stale: true, scan_succeeded: false },
          { status },
        );
        res.headers.set("X-Sync-Warning", "Radon API unavailable - matching cached theta harvester attached");
        return setNoStoreResponseHeaders(res, requestId);
      }
    } catch {
      // Preserve the upstream failure below.
    }
    const message = err instanceof Error ? err.message : "Theta harvester scan failed";
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...emptyThetaHarvesterPayload(), scan_succeeded: false, error: message }, { status }),
      requestId,
    );
  }
}
