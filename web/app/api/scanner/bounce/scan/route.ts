import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders, scrubSecrets } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { tickersBodyToRaw, validateTickerList } from "@/lib/scanTickerList";
import { missingBouncePayload, readBounceCache } from "../route";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 600;
export const radonCapability = "read.spawn";

function cacheMatchesRequest(cached: Record<string, unknown>, tickers: string[], preset: string): boolean {
  if (tickers.length > 0) {
    const requested = Array.isArray(cached.requested_tickers) ? cached.requested_tickers : [];
    return requested.length === tickers.length && tickers.every((ticker, idx) => requested[idx] === ticker);
  }
  const universe = typeof cached.universe === "string" ? cached.universe.toLowerCase() : "";
  const key = preset.toLowerCase();
  return universe === key || universe === `preset:${key}` || universe === `fallback:${key}`;
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "scanner/bounce/scan:route", limit: 20, windowMs: 60_000 }, durableRateTier: "B" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  let body: Record<string, unknown> = {};
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    // Empty body is valid: default preset.
  }

  const params = new URLSearchParams();
  const rawTickers = tickersBodyToRaw(body.tickers);
  let tickers: string[] = [];
  let preset = "largecaps";
  if (rawTickers.trim().length > 0) {
    const parsed = validateTickerList(rawTickers);
    if (!parsed.ok) {
      return setNoStoreResponseHeaders(
        NextResponse.json({ ...missingBouncePayload(), error: parsed.error }, { status: 400 }),
        requestId,
      );
    }
    tickers = parsed.tickers;
    params.set("tickers", tickers.join(","));
  } else {
    preset = typeof body.preset === "string" && body.preset.trim() ? body.preset.trim() : "largecaps";
    params.set("preset", preset);
  }

  try {
    const data = await radonFetch<Record<string, unknown>>(`/bounce-setup/scan?${params.toString()}`, {
      method: "POST",
      timeout: 490_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json({ ...data, scan_succeeded: true }), requestId);
  } catch (err) {
    const status = err instanceof RadonApiError ? err.status : 502;
    if (status >= 500) try {
      const cached = await readBounceCache();
      if (cached && cacheMatchesRequest(cached, tickers, preset)) {
        const res = NextResponse.json(
          { ...cached, is_stale: true, scan_succeeded: false },
          { status },
        );
        res.headers.set("X-Sync-Warning", "Radon API unavailable - matching cached bounce setup attached");
        return setNoStoreResponseHeaders(res, requestId);
      }
    } catch {
      // Preserve the upstream failure below.
    }
    const message = scrubSecrets(err instanceof Error ? err.message : "Bounce setup scan failed");
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...missingBouncePayload(), scan_succeeded: false, error: message }, { status }),
      requestId,
    );
  }
}
