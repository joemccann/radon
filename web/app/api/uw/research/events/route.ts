import { requireRouteAccess } from "@/lib/routeAccess";
import { radonFetchResponse } from "@/lib/radonApi";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "internal";
export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {operatorOnly:true});
  if (!access.ok) return access.response;
  const ticker = (new URL(request.url).searchParams.get("ticker") || "SPY").toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)) return Response.json({error:"Invalid ticker"},{status:400});
  try {
    const upstream = await radonFetchResponse(`/uw/research/events?ticker=${encodeURIComponent(ticker)}`, {token:access.principal.token, signal:request.signal, timeout:60_000});
    return new Response(upstream.body,{headers:{"Content-Type":"text/event-stream","Cache-Control":"private, no-store","X-Accel-Buffering":"no"}});
  } catch {
    return Response.json({error:"Research stream is unavailable. Retained observations remain available."},{status:503});
  }
}
