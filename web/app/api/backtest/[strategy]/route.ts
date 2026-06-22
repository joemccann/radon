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

export async function GET(
  _request: Request,
  context: { params: Promise<{ strategy: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  const { strategy } = await context.params;
  try {
    const data = await radonFetch(`/backtest/${encodeURIComponent(strategy)}`, {
      timeout: 60_000,
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
