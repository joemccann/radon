import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

/**
 * GET /api/backtest/[strategy]
 *
 * Proxies the FastAPI walk-forward backtester (F12). Returns the latest
 * persisted run for a strategy (or runs one when none exists). 200 + a
 * `status` flag for legitimate empty / not-wired states, never 4xx — so the
 * browser console stays quiet for a strategy that is registered but not yet
 * wired for backtesting.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const STRATEGIES = new Set([
  "cri",
  "dark_pool_flow",
  "garch_convergence",
  "leap_iv",
  "risk_reversal",
  "vcg",
]);

export async function GET(
  request: Request,
  context: { params: Promise<{ strategy: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  const access = await requireRouteAccess(request, {
    rate: { key: "backtest", limit: 4, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const { strategy } = await context.params;
  if (!STRATEGIES.has(strategy)) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ status: "unknown_strategy", strategy }, { status: 400 }),
      requestId,
    );
  }
  try {
    const data = await radonFetch(`/backtest/${encodeURIComponent(strategy)}`, {
      timeout: 190_000,
      signal: request.signal,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch {
    return setNoStoreResponseHeaders(
      NextResponse.json({
        strategy,
        status: "unavailable",
        trades: [],
        metrics: null,
      }),
      requestId,
    );
  }
}
