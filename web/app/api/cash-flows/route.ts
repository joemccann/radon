import { NextRequest, NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

// Disable Next.js static caching: cash flows update once per day but the
// Turso query is cheap. Without this, the framework freezes the first
// response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const days = url.searchParams.get("days") ?? "90";
  const types = url.searchParams.get("types") ?? "";
  const requestId = getRequestId();

  try {
    const data = await radonFetch(`/cash-flows?days=${encodeURIComponent(days)}&types=${encodeURIComponent(types)}`);
    const res = NextResponse.json(data);
    setNoStoreResponseHeaders(res, requestId);
    return res;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    const res = NextResponse.json(
      { rows: [], count: 0, summary: null, error: message },
      { status: 502 },
    );
    setNoStoreResponseHeaders(res, requestId);
    return res;
  }
}
