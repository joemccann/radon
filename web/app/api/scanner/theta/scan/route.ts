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
