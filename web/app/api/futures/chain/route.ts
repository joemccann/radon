import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { radonFetch, RadonApiError } from "@/lib/radonApi";

/**
 * Belt-and-suspenders fallback for when FastAPI is down/restarting: read the
 * per-symbol futures-chain cache FastAPI writes (data/futures_chain_{SYMBOL}.json)
 * straight off disk. Mirrors readCachedGexFromDisk in app/api/gex/route.ts.
 */
async function readCachedFuturesChain(
  symbolUpper: string,
): Promise<Record<string, unknown> | null> {
  try {
    const raw = await readFile(
      join(process.cwd(), "..", "data", `futures_chain_${symbolUpper}.json`),
      "utf-8",
    );
    const jsonStart = raw.indexOf("{");
    if (jsonStart === -1) return null;
    const parsed = JSON.parse(raw.slice(jsonStart)) as Record<string, unknown>;
    return Array.isArray(parsed.contracts) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * GET /api/futures/chain?symbol=VIX
 *
 * Proxy to FastAPI /futures/chain. Returns every listed future
 * contract for the symbol with its conId, expiry, exchange, multiplier,
 * and tradingClass. The order form uses conId to disambiguate among
 * multiple listings on the same expiry day (standard monthly vs
 * weekly VIX contracts both expire on Wednesday).
 *
 * Currently scoped to VIX. SPX / NDX futures wiring will land later.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "futures/chain:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol");

  if (!symbol) {
    return setNoStoreResponseHeaders(
      NextResponse.json(
        { error: "symbol parameter required", code: "BAD_REQUEST" },
        { status: 400 },
      ),
      requestId,
    );
  }

  const symbolUpper = symbol.toUpperCase();

  // Defense-in-depth: the symbol is interpolated into a disk cache filename
  // (futures_chain_<SYMBOL>.json) on the fallback path, so reject anything that
  // isn't a plain futures root before it can traverse.
  if (!/^[A-Z0-9.]{1,10}$/.test(symbolUpper)) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "invalid symbol", code: "BAD_REQUEST" }, { status: 400 }),
      requestId,
    );
  }

  try {
    const data = await radonFetch<Record<string, unknown>>(
      `/futures/chain?symbol=${encodeURIComponent(symbolUpper)}`,
      // 28s: room for ib_chain.py's bounded connect/details retries (it rides
      // out transient secdefil/ushmds farm flaps) within the 30s subprocess cap.
      { timeout: 28_000 },
    );
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (err) {
    // FastAPI down/restarting: serve the per-symbol disk cache if present so
    // the order ticket never shows a chain timeout. Only error when no cache.
    const cached = await readCachedFuturesChain(symbolUpper);
    if (cached) {
      const res = NextResponse.json({ ...cached, stale: true });
      res.headers.set(
        "X-Sync-Warning",
        "Futures chain fetch failed - serving cached data",
      );
      return setNoStoreResponseHeaders(res, requestId);
    }

    const status = err instanceof RadonApiError ? err.status : 502;
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Futures chain temporarily unavailable" }, { status }),
      requestId,
    );
  }
}
