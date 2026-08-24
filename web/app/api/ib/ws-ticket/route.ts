import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { requireRouteAccess } from "@/lib/routeAccess";

/**
 * POST /api/ib/ws-ticket
 *
 * R-179: this route made the Bearer OPTIONAL and had no route-local guard,
 * so an anonymous caller reached `radonFetch("/ws-ticket")` with no
 * Authorization header — and the Next.js server IS loopback to FastAPI, whose
 * `/ws-ticket` trusts a loopback caller. The route was a deputy that minted a
 * relay ticket for whoever asked. The ticket is now minted from the
 * authenticated principal's OWN token.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request?: Request) {
  const access = await requireRouteAccess(request, {
    rate: { key: "ib/ws-ticket:route", limit: 30, windowMs: 60_000 },
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch<{ ticket: string }>("/ws-ticket", {
      method: "POST",
      token: access.principal.token,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    const detail = err instanceof Error ? err.message : "WS ticket failed";
    return setNoStoreResponseHeaders(NextResponse.json({ detail }, { status }), requestId);
  }
}
