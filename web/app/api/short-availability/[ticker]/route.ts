import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

// Never cache — live IB probe + UW fetch on every call.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const TICKER_RE = /^[A-Z]{1,6}$/;

type Params = { params: Promise<{ ticker: string }> };

/**
 * GET /api/short-availability/[ticker]
 *
 * Proxy to FastAPI GET /short-availability/{ticker}.
 * Always returns 200 per the missing:true semantics contract
 * (see feedback_http_status_for_real_errors.md).
 */
export async function GET(_req: Request, ctx: Params): Promise<Response> {
  const requestId = getRequestId();
  const { ticker: raw } = await ctx.params;
  const upper = raw.trim().toUpperCase();

  if (!TICKER_RE.test(upper)) {
    return setNoStoreResponseHeaders(
      NextResponse.json(missingPayload(upper), { status: 200 }),
      requestId,
    );
  }

  try {
    const data = await radonFetch(`/short-availability/${upper}`, {
      method: "GET",
      // IB probe has a 6s internal timeout; allow FastAPI overhead on top.
      timeout: 15_000,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch {
    // Per missing:true semantics — never surface 502/503 to the browser.
    return setNoStoreResponseHeaders(
      NextResponse.json(missingPayload(upper)),
      requestId,
    );
  }
}

function missingPayload(ticker: string) {
  return {
    ticker,
    shortable: null,
    difficulty: null,
    shortable_shares: null,
    fee_rate: null,
    rebate_rate: null,
    source: "none",
    as_of: new Date().toISOString(),
    missing: true,
  };
}
