import { NextResponse } from "next/server";
import { RadonApiError, radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

// Disable Next.js static caching: knowledge search is a live FastAPI proxy.
// Without this, Next 16 freezes the first response and serves stale data
// until the dev server restarts.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const SEARCH_TIMEOUT_MS = 30_000;
const MAX_QUERY_CHARS = 500;

type SearchBody = Record<string, unknown>;

function invalidQueryReason(body: SearchBody): string | null {
  const query = body.query;
  if (typeof query !== "string" || !query.trim()) return "query is required";
  if (query.length > MAX_QUERY_CHARS) return `query must be at most ${MAX_QUERY_CHARS} chars`;
  return null;
}

export async function POST(request: Request): Promise<Response> {
  const requestId = getRequestId();

  let body: SearchBody;
  try {
    body = (await request.json()) as SearchBody;
  } catch {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Invalid JSON payload" }, { status: 400 }),
      requestId,
    );
  }

  const reason = invalidQueryReason(body);
  if (reason) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: reason }, { status: 400 }),
      requestId,
    );
  }

  try {
    const data = await radonFetch("/knowledge/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      timeout: SEARCH_TIMEOUT_MS,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    if (error instanceof RadonApiError) {
      return setNoStoreResponseHeaders(
        NextResponse.json({ error: error.detail }, { status: error.status }),
        requestId,
      );
    }
    const message = error instanceof Error ? error.message : "Knowledge search failed";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
