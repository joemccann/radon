import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { emptyThetaHarvesterPayload, readThetaHarvesterCache } from "../route";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  const requestId = getRequestId();
  let body: Record<string, unknown> = {};
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    // Empty body is valid. FastAPI supplies defaults.
  }

  const params = new URLSearchParams();
  const ticker = typeof body.ticker === "string" ? body.ticker.trim().toUpperCase() : "";
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

  const path = params.toString()
    ? `/theta-harvester/scan?${params.toString()}`
    : "/theta-harvester/scan";

  try {
    const data = await radonFetch<Record<string, unknown>>(path, {
      method: "POST",
      timeout: 430_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (err) {
    try {
      const cached = await readThetaHarvesterCache();
      const res = NextResponse.json({ ...cached, is_stale: true });
      res.headers.set("X-Sync-Warning", "Radon API unavailable - serving cached theta harvester data");
      return setNoStoreResponseHeaders(res, requestId);
    } catch {
      const status = err instanceof RadonApiError ? err.status : 502;
      const message = err instanceof Error ? err.message : "Theta harvester scan failed";
      return setNoStoreResponseHeaders(
        NextResponse.json({ ...emptyThetaHarvesterPayload(), error: message }, { status }),
        requestId,
      );
    }
  }
}
