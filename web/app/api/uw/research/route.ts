import { NextResponse } from "next/server";
import { requireRouteAccess } from "@/lib/routeAccess";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = { GET: "read", POST: "read.spawn" };
const SYMBOL = /^[A-Z][A-Z0-9.\-]{0,9}$/;
export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const ticker = (new URL(request.url).searchParams.get("ticker") || "SPY").toUpperCase();
  if (!SYMBOL.test(ticker)) return NextResponse.json({error: "Invalid ticker"}, {status:400});
  try {
    const data = await radonFetch(`/uw/research?ticker=${encodeURIComponent(ticker)}`, {token:access.principal.token, timeout:10_000});
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch {
    return setNoStoreResponseHeaders(NextResponse.json({error:"Research observations could not be loaded. Try again."}, {status:503}), requestId);
  }
}
export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {operatorOnly:true, rate:{key:"uw-research-refresh",limit:4,windowMs:60_000},durableRateTier:"B"});
  if (!access.ok) return access.response;
  let body: unknown;
  try { body = await request.json(); } catch { return NextResponse.json({error:"Invalid refresh request"},{status:400}); }
  try {
    const data = await radonFetch("/uw/research/refresh", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),token:access.principal.token, timeout:125_000});
    return setNoStoreResponseHeaders(NextResponse.json(data), getRequestId());
  } catch (error) {
    const status = error instanceof RadonApiError && [400,422,429].includes(error.status) ? error.status : 503;
    return setNoStoreResponseHeaders(NextResponse.json({error:"Research refresh could not complete. Previous observations remain available."},{status}),getRequestId());
  }
}
