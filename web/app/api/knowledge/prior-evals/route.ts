import { NextResponse } from "next/server";
import { RadonApiError, radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

// Disable Next.js static caching: prior-evals is a live FastAPI proxy.
// Without this, Next 16 freezes the first response and serves stale data
// until the dev server restarts.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const PRIOR_EVALS_TIMEOUT_MS = 30_000;
const TICKER_RE = /^[A-Z0-9]{1,6}$/;

function normalizeTicker(raw: string | null): string | null {
  const upper = (raw ?? "").trim().toUpperCase();
  return TICKER_RE.test(upper) ? upper : null;
}

export async function GET(request: Request): Promise<Response> {
  const requestId = getRequestId();
  const url = new URL(request.url);

  const ticker = normalizeTicker(url.searchParams.get("ticker"));
  if (!ticker) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Invalid or missing ticker" }, { status: 400 }),
      requestId,
    );
  }

  const params = new URLSearchParams({ ticker });
  const limit = url.searchParams.get("limit");
  if (limit) params.set("limit", limit);
  const compact = url.searchParams.get("compact");
  if (compact) params.set("compact", compact);

  try {
    const data = await radonFetch(`/knowledge/prior-evals?${params.toString()}`, {
      timeout: PRIOR_EVALS_TIMEOUT_MS,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    if (error instanceof RadonApiError) {
      return setNoStoreResponseHeaders(
        NextResponse.json({ error: error.detail }, { status: error.status }),
        requestId,
      );
    }
    const message = error instanceof Error ? error.message : "Prior evals lookup failed";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
