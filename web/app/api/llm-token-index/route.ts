import { NextRequest, NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

// Disable Next.js static caching: the index updates once per day via the
// systemd timer, but the underlying Turso read is cheap and we never want
// to serve a stale "no data" payload after the first row lands.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "read";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const days = url.searchParams.get("days") ?? "180";
  const requestId = getRequestId();

  try {
    const data = await radonFetch(`/llm-token-index?days=${encodeURIComponent(days)}`);
    const res = NextResponse.json(data);
    setNoStoreResponseHeaders(res, requestId);
    return res;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    const res = NextResponse.json(
      { rows: [], count: 0, days: Number(days) || 180, error: message },
      { status: 502 },
    );
    setNoStoreResponseHeaders(res, requestId);
    return res;
  }
}
